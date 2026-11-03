"""Backend restart and crash recovery with fresh synthetic SQLite fixtures.

A "restart" is a new create_app() served by a new loopback Uvicorn server over
the same fixture file. The crash case kills a separate Python process while it
holds an uncommitted ingest. Everything is fictional and bound to 127.0.0.1.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier, Timer
import unittest
from urllib.parse import urlencode

from mom_health_backend.app import Settings, create_app
from mom_health_backend.repository import SQLiteRepository

try:
    from synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample, status
except ImportError:  # imported as tests.<module>
    from tests.synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample, status

BACKEND = Path(__file__).resolve().parents[1]

# Runs in a child process: begins a real ingest, then stalls inside the open
# transaction after its third sample INSERT until the parent kills it.
STALLED_WRITER = r'''
import json, sqlite3, sys, time
sys.path.insert(0, sys.argv[1])
from mom_health_backend.models import Batch
from mom_health_backend.repository import SQLiteRepository

class Stall(sqlite3.Connection):
    inserted = 0
    def execute(self, sql, *args):
        result = super().execute(sql, *args)
        if sql.startswith("INSERT INTO samples"):
            Stall.inserted += 1
            if Stall.inserted == 3:
                print("HOLDING", flush=True)
                time.sleep(120)
        return result

repo = SQLiteRepository(sys.argv[2])
repo.connect = lambda: sqlite3.connect(repo.path, timeout=5, factory=Stall)
repo.ingest(Batch.model_validate(json.loads(sys.stdin.readline())))
print("UNEXPECTED COMMIT", flush=True)
'''


class RestartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.database = fixture_dir('restart-fixtures') / 'synthetic.sqlite3'
        self.settings = Settings(sqlite_path=str(self.database), access_token=TOKEN)

    def serve(self):
        return LoopbackServer(create_app(self.settings))

    def get(self, server, path, **params):
        code, payload = server.request('GET', path + ('?' + urlencode(params) if params else ''))
        self.assertEqual(200, code, payload)
        return json.loads(payload)

    def count(self, sql):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(sql).fetchone()[0]

    def test_committed_receipts_samples_and_status_survive_restart(self):
        collided = sample()
        first = batch(collided, sample(), sample(heart_rate_bpm=None, missing_reason='unsupported', model='V4'))
        second = batch(dict(collided, heart_rate_bpm=101), sample())
        with self.serve() as server:
            receipts = [json.loads(server.request('POST', '/v1/batches', b)[1]) for b in (first, second)]
            self.assertEqual([dict(sample_id=collided['sample_id'], reason='sample_id_collision')], receipts[1]['rejected'])
            self.assertEqual(200, server.request('POST', '/v1/gateway-status', status(battery_percent=0))[0])
            before = (self.get(server, '/v1/samples', device_id='synthetic-watch', limit=100),
                      self.get(server, '/v1/devices'), self.get(server, '/v1/gateway-status', device_id='synthetic-watch'))
        with self.serve() as server:
            # The phone may not know whether its acknowledgment arrived before
            # the restart; replay must return the original partial receipt.
            for submitted, receipt in zip((first, second), receipts):
                code, payload = server.request('POST', '/v1/batches', submitted)
                self.assertEqual((200, receipt), (code, json.loads(payload)))
            changed = dict(second, samples=[sample()])
            self.assertEqual(409, server.request('POST', '/v1/batches', changed)[0])
            after = (self.get(server, '/v1/samples', device_id='synthetic-watch', limit=100),
                     self.get(server, '/v1/devices'), self.get(server, '/v1/gateway-status', device_id='synthetic-watch'))
        self.assertEqual(before, after)
        self.assertEqual(0, after[2]['status']['battery_percent'])
        self.assertEqual(4, self.count('SELECT COUNT(*) FROM samples'))
        self.assertEqual(2, self.count('SELECT COUNT(*) FROM batches'))

    def test_cursors_issued_before_restart_continue_after_restart_and_new_inserts(self):
        rows = [sample(received_at='2026-10-22T00:%02d:00Z' % i) for i in range(30)]
        ids = [row['sample_id'] for row in rows]
        with self.serve() as server:
            self.assertEqual(200, server.request('POST', '/v1/batches', batch(*rows))[0])
            for device in ('synthetic-a', 'synthetic-b'):
                self.assertEqual(200, server.request('POST', '/v1/batches', batch(sample(device_id=device)))[0])
            ascending = self.get(server, '/v1/samples', device_id='synthetic-watch', limit=10)
            descending = self.get(server, '/v1/samples', device_id='synthetic-watch', limit=10, order='desc')
            listing = self.get(server, '/v1/devices', limit=1)
        self.assertEqual(ids[:10], [s['sample_id'] for s in ascending['samples']])
        self.assertEqual(ids[:19:-1], [s['sample_id'] for s in descending['samples']])
        self.assertEqual(['synthetic-a'], [d['device_id'] for d in listing['devices']])
        later = [sample() for _ in range(5)]
        with self.serve() as server:
            self.assertEqual(200, server.request('POST', '/v1/batches', batch(*later))[0])
            self.assertEqual(200, server.request('POST', '/v1/batches', batch(sample(device_id='synthetic-c')))[0])
            rest_up = self.get(server, '/v1/samples', device_id='synthetic-watch', limit=100, cursor=ascending['next_cursor'])
            rest_down = self.get(server, '/v1/samples', device_id='synthetic-watch', limit=100, order='desc', cursor=descending['next_cursor'])
            rest_devices = self.get(server, '/v1/devices', limit=100, cursor=listing['next_cursor'])
            # A cursor stays bound to its device and direction after restart.
            for params in (dict(device_id='synthetic-a', cursor=ascending['next_cursor']),
                           dict(device_id='synthetic-watch', order='desc', cursor=ascending['next_cursor'])):
                self.assertEqual(422, server.request('GET', '/v1/samples?' + urlencode(params))[0])
        self.assertEqual(ids[10:] + [s['sample_id'] for s in later], [s['sample_id'] for s in rest_up['samples']])
        self.assertEqual(ids[19::-1], [s['sample_id'] for s in rest_down['samples']])
        self.assertIsNone(rest_up['next_cursor'])
        self.assertIsNone(rest_down['next_cursor'])
        self.assertEqual(['synthetic-b', 'synthetic-c', 'synthetic-watch'], [d['device_id'] for d in rest_devices['devices']])

    def test_concurrent_first_start_initializes_one_schema(self):
        gate = Barrier(8)

        def start(_):
            gate.wait(15)
            SQLiteRepository(self.database).initialize()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(start, range(8)))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual([(1,), (2,)], connection.execute('SELECT version FROM schema_versions ORDER BY version').fetchall())
            tables = sorted(r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        self.assertEqual(['batches', 'gateway_status', 'samples', 'schema_versions', 'sqlite_sequence'], tables)
        with self.serve() as server:
            self.assertEqual('ready', self.get(server, '/v1/health')['status'])

    def test_startup_waits_for_a_brief_writer_and_fails_closed_on_a_persistent_one(self):
        with self.serve() as server:
            self.assertEqual(200, server.request('POST', '/v1/batches', batch(sample()))[0])
        holder = sqlite3.connect(self.database, isolation_level=None, check_same_thread=False)
        self.addCleanup(holder.close)
        holder.execute('BEGIN IMMEDIATE')
        release = Timer(0.5, lambda: holder.execute('ROLLBACK'))
        release.start()
        self.addCleanup(release.cancel)
        with self.serve() as server:  # waits within the 5 s busy timeout
            self.assertEqual('ready', self.get(server, '/v1/health')['status'])
        release.join()
        holder.execute('BEGIN IMMEDIATE')  # now held past the busy timeout
        with self.assertRaisesRegex(RuntimeError, 'failed to start'):
            with self.serve():
                pass
        holder.execute('ROLLBACK')
        with self.serve() as server:
            self.assertEqual(1, self.get(server, '/v1/devices')['devices'][0]['sample_count'])

    def test_writer_killed_inside_an_ingest_leaves_nothing_and_the_retry_commits_once(self):
        SQLiteRepository(self.database).initialize()
        submitted = batch(*[sample() for _ in range(5)])
        child = subprocess.Popen([sys.executable, '-c', STALLED_WRITER, str(BACKEND), str(self.database)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            child.stdin.write(json.dumps(submitted) + '\n')
            child.stdin.flush()
            self.assertEqual('HOLDING', child.stdout.readline().strip())
            journal = Path(str(self.database) + '-journal')
            self.assertTrue(journal.exists(), 'expected an open rollback journal mid-transaction')
        finally:
            child.kill()
            child.wait(15)
            child.stdin.close()
            child.stdout.close()
        with self.serve() as server:
            self.assertEqual([], self.get(server, '/v1/samples', device_id='synthetic-watch', limit=100)['samples'])
            code, payload = server.request('POST', '/v1/batches', submitted)
            self.assertEqual(200, code)
            self.assertEqual([s['sample_id'] for s in submitted['samples']], json.loads(payload)['accepted_ids'])
            self.assertEqual(json.loads(payload), json.loads(server.request('POST', '/v1/batches', submitted)[1]))
        self.assertEqual(5, self.count('SELECT COUNT(*) FROM samples'))
        self.assertEqual(1, self.count('SELECT COUNT(*) FROM batches'))
        self.assertFalse(Path(str(self.database) + '-journal').exists())


if __name__ == '__main__':
    unittest.main()
