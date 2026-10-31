"""Candidate-only synthetic SQLite budget checks; no cloud or device access."""
from contextlib import closing
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
import warnings

from fastapi.testclient import TestClient
from mom_health_backend.models import Batch
from railway_candidate.budgeted_sqlite import BudgetConnection, BudgetedSQLiteRepository
from railway_candidate.direct_candidate import create_direct_candidate
from tests.test_backend import sample, batch
from tests.test_direct_adversarial import HOST, USER, PASSWORD, PHONE, bearer


class FakeClock:
    def __init__(self): self.value = 100.0
    def __call__(self): return self.value


class BudgetedSQLiteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2] / 'build' / 'sqlite-budget-tests' / str(uuid4())
        self.root.mkdir(parents=True)
        self.path = self.root / 'synthetic.sqlite3'
        self.clock = FakeClock()
        self.repo = BudgetedSQLiteRepository(self.path, budget_seconds=2, busy_seconds=.1, clock=self.clock)
        self.repo.initialize()

    def test_ack_after_commit_and_lost_response_replay(self):
        item = sample()
        request = Batch.model_validate(batch(item))
        with closing(sqlite3.connect(self.path)) as observer:
            response = self.repo.ingest(request)
            self.assertEqual([item['sample_id']], response['accepted_ids'])
            self.assertEqual(1, observer.execute('SELECT count(*) FROM samples').fetchone()[0])
        self.assertEqual(response, self.repo.ingest(request))
        self.assertEqual([item['sample_id']],
                         self.repo.ingest(Batch.model_validate(batch(item)))['duplicate_ids'])

    def test_expired_budget_rolls_back_without_commit(self):
        with closing(self.repo.connect()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("INSERT INTO samples(sample_id,device_id,synthetic,canonical) VALUES('fixture','test',1,'{}')")
            self.clock.value += 2.0
            with self.assertRaisesRegex(sqlite3.OperationalError, 'budget'):
                conn.commit()
            conn.rollback()
            with self.assertRaisesRegex(sqlite3.OperationalError, 'budget'):
                conn.execute('SELECT 1')
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(0, observer.execute('SELECT count(*) FROM samples').fetchone()[0])

    def test_long_sql_interrupts_and_does_not_commit(self):
        calls = 0
        def advancing_clock():
            nonlocal calls
            calls += 1
            return 100.0 if calls < 8 else 103.0
        repo = BudgetedSQLiteRepository(self.path, budget_seconds=2, busy_seconds=.1,
                                        instruction_interval=1, clock=advancing_clock)
        with closing(repo.connect()) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<100000) SELECT sum(x) FROM c').fetchone()
            conn.rollback()
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(0, observer.execute('SELECT count(*) FROM samples').fetchone()[0])

    def test_progress_interrupts_long_write_and_rolls_back(self):
        state = {'active': False, 'calls': 0}
        def advancing_clock():
            if state['active']:
                state['calls'] += 1
            return 103.0 if state['calls'] >= 300 else 100.0
        repo = BudgetedSQLiteRepository(self.path, budget_seconds=2, busy_seconds=.1,
                                        instruction_interval=1, clock=advancing_clock)
        with closing(repo.connect()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            state['active'] = True
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<10000) "
                             "INSERT INTO samples(sample_id,device_id,synthetic,canonical) "
                             "SELECT printf('fixture-%d',x),'test',1,'{}' FROM c")
            self.assertGreaterEqual(state['calls'], 300)
            conn.rollback()
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(0, observer.execute('SELECT count(*) FROM samples').fetchone()[0])
            self.assertEqual(0, observer.execute('SELECT count(*) FROM batches').fetchone()[0])

    def test_writer_lock_fails_without_ack_then_retry_succeeds(self):
        request = Batch.model_validate(batch(sample()))
        with closing(sqlite3.connect(self.path)) as lock:
            lock.execute('BEGIN IMMEDIATE')
            start = time.monotonic()
            with self.assertRaises(sqlite3.OperationalError):
                self.repo.ingest(request)
            self.assertLess(time.monotonic() - start, 2.0)
            lock.rollback()
        result = self.repo.ingest(request)
        self.assertEqual([request.samples[0].sample_id], result['accepted_ids'])
        self.assertEqual(result, self.repo.ingest(request))

    def test_expiry_mid_batch_rolls_back_samples_and_receipt(self):
        request = Batch.model_validate(batch(sample(), sample()))
        original = self.repo.execute
        def expire_after_first_insert(conn, sql, args=()):
            result = original(conn, sql, args)
            if sql.startswith('INSERT INTO samples'):
                self.clock.value += 2.0
            return result
        with patch.object(self.repo, 'execute', side_effect=expire_after_first_insert):
            with self.assertRaises(sqlite3.OperationalError):
                self.repo.ingest(request)
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(0, observer.execute('SELECT count(*) FROM samples').fetchone()[0])
            self.assertEqual(0, observer.execute('SELECT count(*) FROM batches').fetchone()[0])

    def test_failed_commit_returns_no_ack_and_rolls_back(self):
        request = Batch.model_validate(batch(sample()))
        with patch.object(BudgetConnection, 'commit', side_effect=sqlite3.OperationalError('fixture commit failed')):
            with self.assertRaises(sqlite3.OperationalError):
                self.repo.ingest(request)
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(0, observer.execute('SELECT count(*) FROM samples').fetchone()[0])
            self.assertEqual(0, observer.execute('SELECT count(*) FROM batches').fetchone()[0])

    def test_started_commit_is_not_cancelled_by_progress_deadline(self):
        state = {'active': False, 'calls': 0}
        def advancing_clock():
            if state['active']:
                state['calls'] += 1
            return 103.0 if state['calls'] >= 2 else 100.0
        repo = BudgetedSQLiteRepository(self.path, budget_seconds=2, busy_seconds=.1,
                                        instruction_interval=1, clock=advancing_clock)
        with closing(repo.connect()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("INSERT INTO samples(sample_id,device_id,synthetic,canonical) VALUES('fixture','test',1,'{}')")
            state['active'] = True
            conn.commit()
            self.assertEqual(1, state['calls'])
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(1, observer.execute('SELECT count(*) FROM samples').fetchone()[0])

    def test_error_after_durable_commit_is_uncertain_but_retry_is_idempotent(self):
        request = Batch.model_validate(batch(sample()))
        def commit_then_fail(conn):
            sqlite3.Connection.commit(conn)
            raise sqlite3.OperationalError('fixture response lost after commit')
        with patch.object(BudgetConnection, 'commit', autospec=True, side_effect=commit_then_fail):
            with self.assertRaises(sqlite3.OperationalError):
                self.repo.ingest(request)
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(1, observer.execute('SELECT count(*) FROM samples').fetchone()[0])
            self.assertEqual(1, observer.execute('SELECT count(*) FROM batches').fetchone()[0])
        response = self.repo.ingest(request)
        self.assertEqual([request.samples[0].sample_id], response['accepted_ids'])
        self.assertEqual(response, self.repo.ingest(request))

    def test_cursor_api_is_not_a_general_budget_sandbox(self):
        with closing(self.repo.connect()) as conn:
            self.clock.value += 2.0
            # The trusted repository uses Connection.execute instead. Do not
            # expose a connection to untrusted arbitrary SQL callers.
            self.assertEqual((1,), conn.cursor().execute('SELECT 1').fetchone())
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('SELECT 1')

    def test_http_busy_write_has_no_ack_and_retries_idempotently(self):
        with patch('railway_candidate.direct_candidate.validate_storage', return_value=str(self.path)):
            app = create_direct_candidate(HOST, USER, PASSWORD, PHONE, str(self.path), str(self.root),
                                          repository=self.repo)
        body = batch(sample())
        headers = {'host': HOST, 'authorization': bearer(PHONE)}
        with TestClient(app, client=('127.0.0.1', 1234)) as client:
            with closing(sqlite3.connect(self.path)) as lock:
                lock.execute('BEGIN IMMEDIATE')
                failed = client.post('/v1/batches', headers=headers, json=body)
                self.assertEqual(503, failed.status_code)
                self.assertNotIn('accepted_ids', failed.json())
                lock.rollback()
            first = client.post('/v1/batches', headers=headers, json=body)
            self.assertEqual(200, first.status_code)
            self.assertEqual([body['samples'][0]['sample_id']], first.json()['accepted_ids'])
            self.assertEqual(first.json(), client.post('/v1/batches', headers=headers, json=body).json())

    def test_real_data_rejected_before_connection_and_no_override(self):
        mixed = Batch.model_validate(batch(sample(), sample(synthetic=False)))
        with patch.object(self.repo, 'connect', side_effect=AssertionError('must not connect')):
            response = self.repo.ingest(mixed)
            self.assertEqual([], response['accepted_ids'])
            with self.assertRaisesRegex(ValueError, 'disabled'):
                self.repo.ingest(mixed, allow_real=True)
            with self.assertRaisesRegex(ValueError, 'disabled'):
                self.repo.devices(allow_real=True)

    def test_invalid_budgets_and_clock_fail_closed(self):
        for kwargs in ({'budget_seconds': 0}, {'budget_seconds': float('nan')},
                       {'busy_seconds': 3}, {'instruction_interval': 0}, {'clock': None},
                       {'require_existing': 'true'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BudgetedSQLiteRepository(self.path, **kwargs)
        self.clock.value = float('nan')
        with self.assertRaisesRegex(ValueError, 'clock'):
            self.repo.connect()

    def test_existing_only_requires_absolute_path_and_wont_create_missing_file(self):
        with self.assertRaisesRegex(ValueError, 'absolute'):
            BudgetedSQLiteRepository('relative.sqlite3', require_existing=True)
        missing = self.root / 'never-created.sqlite3'
        repo = BudgetedSQLiteRepository(missing, require_existing=True)
        with self.assertRaises(sqlite3.OperationalError):
            repo.connect()
        self.assertFalse(missing.exists())
        if os.name == 'nt':
            with self.assertRaisesRegex(ValueError, 'drive-letter'):
                BudgetedSQLiteRepository(r'\\server\share\fixture.sqlite3', require_existing=True)

    def test_existing_only_does_not_recreate_a_disappeared_fixture(self):
        target = self.root / 'existing.sqlite3'
        retained = self.root / 'retained-original.sqlite3'
        with closing(sqlite3.connect(target)) as conn:
            conn.execute('CREATE TABLE proof(value TEXT)')
            conn.execute("INSERT INTO proof VALUES ('synthetic fixture')")
            conn.commit()
        repo = BudgetedSQLiteRepository(target, require_existing=True)
        with closing(repo.connect()) as conn:
            self.assertEqual(('synthetic fixture',), conn.execute('SELECT value FROM proof').fetchone())
        # Preserve the fixture while simulating its disappearance from the
        # original pathname; no file is deleted or overwritten.
        target.rename(retained)
        with self.assertRaises(sqlite3.OperationalError):
            repo.connect()
        self.assertFalse(target.exists())
        with closing(sqlite3.connect(retained)) as conn:
            self.assertEqual(('synthetic fixture',), conn.execute('SELECT value FROM proof').fetchone())

    def test_existing_only_escapes_uri_sensitive_local_path(self):
        special_dir = self.root / 'space # percent % unicode Ω'
        special_dir.mkdir()
        target = special_dir / 'db # percent %.sqlite3'
        with closing(sqlite3.connect(target)) as conn:
            conn.execute('CREATE TABLE proof(value INTEGER)')
            conn.execute('INSERT INTO proof VALUES(17)')
            conn.commit()
        repo = BudgetedSQLiteRepository(target, require_existing=True)
        with closing(repo.connect()) as conn:
            self.assertEqual((17,), conn.execute('SELECT value FROM proof').fetchone())
        # Pure-path tests exercise URI escaping syntax on both host path styles;
        # they do not claim an actual cross-platform SQLite runtime test.
        # PurePath URI conversion is used only as a cross-platform syntax
        # oracle here; actual connections above use concrete Path objects.
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=DeprecationWarning, module=__name__)
            uris = (PurePosixPath('/tmp/db # %?name.sqlite3').as_uri(),
                    PureWindowsPath('C:/data/db # %?name.sqlite3').as_uri())
        for uri in uris:
            self.assertIn('%23', uri)
            self.assertIn('%25', uri)
            self.assertIn('%3F', uri)
            self.assertNotIn('?', uri)


if __name__ == '__main__': unittest.main()
