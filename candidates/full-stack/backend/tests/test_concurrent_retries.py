"""Concurrent and retried synthetic submissions against the real SQLite store.

Each test uses a fresh fixture database and fictional batches. Workers start
together behind a barrier so their transactions genuinely contend; assertions
concern stored rows and acknowledgments, never timing. No phone, watch,
external network or real health data is involved.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import threading
from threading import Barrier
import unittest
from uuid import uuid4


from unittest.mock import patch

from mom_health_backend.app import Settings, create_app
from mom_health_backend.models import Batch
from mom_health_backend.repository import BatchConflict, SQLiteRepository
from railway_candidate.budgeted_sqlite import BudgetedSQLiteRepository
from railway_candidate.direct_candidate import MAX_IN_FLIGHT, create_direct_candidate

try:
    from synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample
except ImportError:  # imported as tests.<module>
    from tests.synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample

WORKERS = 24


def race(count, action):
    """Run action(i) for i in range(count) concurrently; capture results and errors."""
    gate = Barrier(count)

    def run(index):
        gate.wait(15)
        try:
            return 'ok', action(index)
        except Exception as exc:  # recorded and asserted by the caller
            return 'error', exc
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


class RepositoryRaceTests(unittest.TestCase):
    def setUp(self):
        self.path = fixture_dir('concurrency-fixtures') / 'synthetic.sqlite3'
        self.repo = SQLiteRepository(self.path)
        self.repo.initialize()

    def rows(self, sql, args=()):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(sql, args).fetchall()

    def receipts(self, results):
        self.assertEqual([], [value for kind, value in results if kind != 'ok'])
        return [value for _, value in results]

    def test_identical_batch_raced_by_many_retries_is_stored_once_with_one_receipt(self):
        submitted = Batch.model_validate(batch(sample(), sample(heart_rate_bpm=None, missing_reason='no_result'), sample()))
        receipts = self.receipts(race(WORKERS, lambda _: self.repo.ingest(submitted)))
        for receipt in receipts:
            self.assertEqual(receipts[0], receipt)
        self.assertEqual([s.sample_id for s in submitted.samples], receipts[0]['accepted_ids'])
        self.assertEqual([], receipts[0]['duplicate_ids'] + receipts[0]['rejected'])
        self.assertEqual([(3,)], self.rows('SELECT COUNT(*) FROM samples'))
        self.assertEqual([(1,)], self.rows('SELECT COUNT(*) FROM batches'))

    def test_overlapping_batches_raced_in_different_orders_accept_each_sample_once(self):
        shared = [sample() for _ in range(5)]
        batches = [Batch.model_validate(batch(*(shared[k % 5:] + shared[:k % 5]))) for k in range(12)]
        receipts = self.receipts(race(len(batches), lambda i: self.repo.ingest(batches[i])))
        accepted = [sample_id for receipt in receipts for sample_id in receipt['accepted_ids']]
        self.assertCountEqual([s['sample_id'] for s in shared], accepted)
        for receipt, submitted in zip(receipts, batches):
            self.assertCountEqual([s.sample_id for s in submitted.samples], receipt['accepted_ids'] + receipt['duplicate_ids'])
            self.assertEqual([], receipt['rejected'])
        self.assertEqual([(5,)], self.rows('SELECT COUNT(*) FROM samples'))
        self.assertEqual([(12,)], self.rows('SELECT COUNT(*) FROM batches'))
        # Every acknowledgment was durably recorded: a later retry of any batch
        # returns exactly what that batch was told.
        for receipt, submitted in zip(receipts, batches):
            self.assertEqual(receipt, self.repo.ingest(submitted))

    def test_conflicting_contents_for_one_sample_id_store_only_the_winner(self):
        sample_id = str(uuid4())
        variants = [Batch.model_validate(batch(sample(sample_id=sample_id, heart_rate_bpm=60 + i))) for i in range(12)]
        receipts = self.receipts(race(len(variants), lambda i: self.repo.ingest(variants[i])))
        winners = [i for i, receipt in enumerate(receipts) if receipt['accepted_ids'] == [sample_id]]
        self.assertEqual(1, len(winners))
        for i, receipt in enumerate(receipts):
            if i != winners[0]:
                self.assertEqual([dict(sample_id=sample_id, reason='sample_id_collision')], receipt['rejected'])
                self.assertEqual([], receipt['accepted_ids'] + receipt['duplicate_ids'])
        stored = json.loads(self.rows('SELECT canonical FROM samples WHERE sample_id=?', (sample_id,))[0][0])
        self.assertEqual(60 + winners[0], stored['heart_rate_bpm'])
        self.assertEqual([(1,)], self.rows('SELECT COUNT(*) FROM samples'))

    def test_one_batch_id_reused_with_different_contents_has_one_winner(self):
        batch_id = str(uuid4())
        variants = [Batch.model_validate(batch(sample(), batch_id=batch_id)) for _ in range(12)]
        results = race(len(variants), lambda i: self.repo.ingest(variants[i]))
        winners = [i for i, (kind, _) in enumerate(results) if kind == 'ok']
        self.assertEqual(1, len(winners))
        losers = [value for kind, value in results if kind == 'error']
        self.assertEqual(11, len(losers))
        self.assertTrue(all(isinstance(error, BatchConflict) for error in losers), losers)
        winner = variants[winners[0]].samples[0].sample_id
        self.assertEqual([winner], results[winners[0]][1]['accepted_ids'])
        self.assertEqual([(winner,)], self.rows('SELECT sample_id FROM samples'))
        self.assertEqual([(1,)], self.rows('SELECT COUNT(*) FROM batches'))

    def test_partial_collision_receipt_is_the_same_for_every_concurrent_retry(self):
        collided, repeated, fresh = sample(), sample(), sample()
        self.repo.ingest(Batch.model_validate(batch(collided, repeated)))
        submitted = Batch.model_validate(batch(dict(collided, heart_rate_bpm=99), fresh, repeated))
        receipts = self.receipts(race(16, lambda _: self.repo.ingest(submitted)))
        expected = dict(batch_id=submitted.batch_id, accepted_ids=[fresh['sample_id']], duplicate_ids=[repeated['sample_id']],
                        rejected=[dict(sample_id=collided['sample_id'], reason='sample_id_collision')])
        for receipt in receipts:
            self.assertEqual(expected, receipt)
        self.assertEqual([(3,)], self.rows('SELECT COUNT(*) FROM samples'))
        self.assertEqual(80, json.loads(self.rows('SELECT canonical FROM samples WHERE sample_id=?', (collided['sample_id'],))[0][0])['heart_rate_bpm'])


class CommitThenFail(sqlite3.Connection):
    """Commits durably, then raises once: the caller cannot know the outcome."""
    armed = False

    def commit(self):
        super().commit()
        if CommitThenFail.armed:
            CommitThenFail.armed = False
            raise sqlite3.OperationalError('synthetic fault after a durable commit')


class AmbiguousOutcomeTests(unittest.TestCase):
    def test_no_acknowledgment_after_a_durable_commit_then_the_retry_gets_the_receipt(self):
        database = fixture_dir('concurrency-fixtures') / 'synthetic.sqlite3'
        repository = SQLiteRepository(database)
        repository.connect = lambda: sqlite3.connect(repository.path, timeout=5, factory=CommitThenFail)
        self.addCleanup(setattr, CommitThenFail, 'armed', False)
        submitted = batch(*[sample() for _ in range(4)])
        with LoopbackServer(create_app(Settings(sqlite_path=str(database), access_token=TOKEN), repository=repository)) as server:
            CommitThenFail.armed = True
            code, payload = server.request('POST', '/v1/batches', submitted)
            self.assertEqual(503, code)
            self.assertNotIn('accepted_ids', json.loads(payload))
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0])
            code, payload = server.request('POST', '/v1/batches', submitted)
            self.assertEqual(200, code)
            self.assertEqual([s['sample_id'] for s in submitted['samples']], json.loads(payload)['accepted_ids'])
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(4, connection.execute('SELECT COUNT(*) FROM samples').fetchone()[0])


class GatedBudgetedRepository(BudgetedSQLiteRepository):
    """Real budgeted SQLite storage; each ingest waits at a test gate first."""

    def __init__(self, path):
        super().__init__(path)
        self.entered, self.lock = 0, threading.Lock()
        self.full, self.release = threading.Event(), threading.Event()

    def ingest(self, batch, allow_real=False):
        with self.lock:
            self.entered += 1
            if self.entered == MAX_IN_FLIGHT:
                self.full.set()
        if not self.release.wait(20):
            raise TimeoutError('synthetic test gate was never released')
        return super().ingest(batch, allow_real)


class DirectCandidateAdmissionTests(unittest.TestCase):
    """The inactive direct-auth candidate on real loopback HTTP with real storage."""
    HOST = 'synthetic.example.com'
    PHONE = 'fictional-phone-token-for-admission-tests-0123456'

    def test_busy_or_storage_503s_never_coincide_with_a_commit_and_retries_converge(self):
        database = fixture_dir('concurrency-fixtures') / 'mom-health-synthetic.sqlite3'
        repository = GatedBudgetedRepository(database)
        with patch('railway_candidate.direct_candidate.validate_storage', return_value=str(database)):
            app = create_direct_candidate(self.HOST, 'fictional-caregiver', 'fictional-caregiver-password-for-admission-1',
                                          self.PHONE, str(database), str(database.parent), repository=repository)
        headers = {'Host': self.HOST}
        bodies = [batch(*[sample(device_id='synthetic-admit-' + str(i)) for _ in range(20)]) for i in range(MAX_IN_FLIGHT + 8)]
        post = lambda server, body: server.request('POST', '/v1/batches', body, token=self.PHONE, headers=headers)
        with LoopbackServer(app) as server, ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT) as pool:
            admitted = [pool.submit(post, server, body) for body in bodies[:MAX_IN_FLIGHT]]
            self.assertTrue(repository.full.wait(20), 'admitted requests never reached storage')
            overflow = [post(server, body) for body in bodies[MAX_IN_FLIGHT:]]
            self.assertEqual([503] * 8, [code for code, _ in overflow])
            self.assertTrue(all(json.loads(payload)['detail'] == 'Service busy; retry later' for _, payload in overflow))
            repository.release.set()
            first = [future.result(30) for future in admitted] + overflow
            with closing(sqlite3.connect(database)) as connection:
                receipts = dict(connection.execute('SELECT batch_id, response FROM batches').fetchall())
                stored = dict(connection.execute('SELECT device_id, COUNT(*) FROM samples GROUP BY device_id').fetchall())
            for body, (code, payload) in zip(bodies, first):
                device = body['samples'][0]['device_id']
                if code == 200:
                    self.assertEqual(json.loads(receipts[body['batch_id']]), json.loads(payload))
                    self.assertEqual(20, stored[device])
                else:
                    # No acknowledgment was issued, so nothing may be committed.
                    self.assertEqual(503, code)
                    self.assertNotIn('accepted_ids', json.loads(payload))
                    self.assertNotIn(body['batch_id'], receipts)
                    self.assertNotIn(device, stored)
            for body, (code, payload) in zip(bodies, first):
                for _ in range(20):
                    if code == 200:
                        break
                    code, payload = post(server, body)
                self.assertEqual(200, code)
                self.assertEqual(20, len(json.loads(payload)['accepted_ids'] + json.loads(payload)['duplicate_ids']))
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(len(bodies), connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0])
            self.assertEqual(20 * len(bodies), connection.execute('SELECT COUNT(*) FROM samples').fetchone()[0])


class LoopbackRetryTests(unittest.TestCase):
    """Real HTTP/1.1 over 127.0.0.1 against the unmodified app and SQLite."""

    def setUp(self):
        database = fixture_dir('concurrency-fixtures') / 'synthetic.sqlite3'
        self.app = create_app(Settings(sqlite_path=str(database), access_token=TOKEN))

    def test_concurrent_http_retries_get_one_acknowledgment_and_one_stored_copy(self):
        body = batch(*[sample() for _ in range(20)])
        with LoopbackServer(self.app) as server:
            results = race(16, lambda _: server.request('POST', '/v1/batches', body))
            self.assertEqual([], [value for kind, value in results if kind != 'ok'])
            self.assertEqual({200}, {status for _, (status, _) in results})
            receipts = [json.loads(payload) for _, (_, payload) in results]
            for receipt in receipts:
                self.assertEqual(receipts[0], receipt)
            self.assertEqual([s['sample_id'] for s in body['samples']], receipts[0]['accepted_ids'])
            status, page = server.request('GET', '/v1/samples?device_id=synthetic-watch&limit=100')
            self.assertEqual(200, status)
            self.assertEqual(sorted(s['sample_id'] for s in body['samples']),
                             sorted(s['sample_id'] for s in json.loads(page)['samples']))

    def test_concurrent_http_batch_id_reuse_yields_one_200_and_409s_without_partial_writes(self):
        batch_id = str(uuid4())
        bodies = [batch(sample(device_id='synthetic-race-' + str(i)), sample(device_id='synthetic-race-' + str(i)), batch_id=batch_id) for i in range(10)]
        with LoopbackServer(self.app) as server:
            results = race(len(bodies), lambda i: server.request('POST', '/v1/batches', bodies[i]))
            statuses = [value[0] for _, value in results]
            self.assertEqual(1, statuses.count(200), statuses)
            self.assertEqual(9, statuses.count(409), statuses)
            winner = statuses.index(200)
            status, listing = server.request('GET', '/v1/devices?limit=100')
            self.assertEqual(200, status)
            devices = {d['device_id']: d['sample_count'] for d in json.loads(listing)['devices']}
            self.assertEqual({'synthetic-race-' + str(winner): 2}, devices)


if __name__ == '__main__':
    unittest.main()
