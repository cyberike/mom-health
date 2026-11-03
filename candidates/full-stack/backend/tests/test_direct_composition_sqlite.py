"""Real synthetic SQLite integration; mount recognition is simulated.

Every run creates a unique workspace fixture. It must never target user data.
No server socket or physical device is used.
"""
import base64
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from railway_candidate.budgeted_sqlite import BudgetedSQLiteRepository
from railway_candidate.direct_local_composition import build_budgeted_local_config


class DirectCompositionSQLiteChecks(unittest.TestCase):
    def test_budgeted_fixture_ingest_read_lock_and_idempotent_retry(self):
        root = (Path(__file__).resolve().parents[2] / 'build' /
                'direct-composition-fixtures' / str(uuid4())).resolve()
        root.mkdir(parents=True, exist_ok=False)
        database = root / 'mom-health-synthetic.sqlite3'
        with database.open('xb'):
            pass
        if os.name == 'posix':
            # The existing-only validator demands an owner-only root and file on
            # POSIX. The default umask (0755/0644) is correctly rejected; this
            # test previously ran only on Windows, where that branch is skipped.
            root.chmod(0o700)
            database.chmod(0o600)
        host = 'synthetic.example.com'
        user = 'fictional-caregiver'
        password = 'fictional-caregiver-password-1234567890'
        token = 'fictional-phone-token-12345678901234567890'
        read_auth = 'Basic ' + base64.b64encode((user + ':' + password).encode('ascii')).decode('ascii')
        read_headers = {'host': host, 'authorization': read_auth}
        write_headers = {'host': host, 'authorization': 'Bearer ' + token}
        seen = []
        original_connect = BudgetedSQLiteRepository.connect

        def tracked_connect(repository):
            seen.append(repository.path)
            return original_connect(repository)

        sample = {'sample_id': str(uuid4()), 'device_id': str(uuid4()),
                  'session_id': str(uuid4()), 'model': 'V8', 'source': 'live',
                  'received_at': '2026-01-01T00:00:00Z', 'measured_at': None,
                  'heart_rate_bpm': 72, 'missing_reason': None, 'synthetic': True}
        batch = {'schema_version': 1, 'batch_id': str(uuid4()), 'samples': [sample]}
        retry_sample = dict(sample, sample_id=str(uuid4()))
        retry_batch = {'schema_version': 1, 'batch_id': str(uuid4()), 'samples': [retry_sample]}

        # The fixture directory is not an OS mount. This stub does not test
        # actual mount identity, POSIX ACLs or Railway volume security.
        with patch('railway_candidate.gateway.os.path.ismount', return_value=True), \
             patch.object(BudgetedSQLiteRepository, 'connect', new=tracked_connect):
            config = build_budgeted_local_config(host, user, password, token, str(database), str(root))
            self.assertEqual([], seen)  # no SQLite connection during composition
            with TestClient(config.app, client=('127.0.0.1', 1234)) as client:
                first = client.post('/v1/batches', headers=write_headers, json=batch)
                self.assertEqual(200, first.status_code)
                self.assertEqual([sample['sample_id']], first.json()['accepted_ids'])
                self.assertEqual(first.json(), client.post('/v1/batches', headers=write_headers, json=batch).json())
                response = client.get('/v1/samples?device_id=' + sample['device_id'], headers=read_headers)
                self.assertEqual(200, response.status_code)
                self.assertEqual([sample['sample_id']], [row['sample_id'] for row in response.json()['samples']])
                with closing(sqlite3.connect(database, timeout=0.1)) as lock:
                    lock.execute('BEGIN IMMEDIATE')
                    started = time.monotonic()
                    unavailable = client.post('/v1/batches', headers=write_headers, json=retry_batch)
                    elapsed = time.monotonic() - started
                    self.assertEqual(503, unavailable.status_code)
                    self.assertLess(elapsed, 3.5)
                    lock.rollback()
                committed = client.post('/v1/batches', headers=write_headers, json=retry_batch)
                self.assertEqual(200, committed.status_code)
                self.assertEqual([retry_sample['sample_id']], committed.json()['accepted_ids'])
                self.assertEqual(committed.json(), client.post('/v1/batches', headers=write_headers, json=retry_batch).json())
        self.assertGreaterEqual(len(seen), 5)
        self.assertEqual({str(database)}, set(seen))

    @unittest.skipUnless(os.name == 'posix', 'real POSIX owner/mode checks')
    def test_default_umask_fixture_is_rejected_before_any_connection(self):
        # Real (unmocked) Linux metadata: a group/other-readable root or file must
        # fail closed before the repository ever opens SQLite.
        for root_mode, file_mode in ((0o755, 0o600), (0o700, 0o644)):
            with self.subTest(root=oct(root_mode), file=oct(file_mode)):
                root = (Path(__file__).resolve().parents[2] / 'build' /
                        'direct-composition-fixtures' / str(uuid4())).resolve()
                root.mkdir(parents=True, exist_ok=False)
                database = root / 'mom-health-synthetic.sqlite3'
                with database.open('xb'):
                    pass
                root.chmod(root_mode)
                database.chmod(file_mode)
                seen = []
                original_connect = BudgetedSQLiteRepository.connect

                def tracked_connect(repository):
                    seen.append(repository.path)
                    return original_connect(repository)

                with patch('railway_candidate.gateway.os.path.ismount', return_value=True), \
                     patch.object(BudgetedSQLiteRepository, 'connect', new=tracked_connect), \
                     self.assertRaises(ValueError):
                    build_budgeted_local_config('synthetic.example.com', 'fictional-caregiver',
                                                'fictional-caregiver-password-1234567890',
                                                'fictional-phone-token-12345678901234567890',
                                                str(database), str(root))
                self.assertEqual([], seen)
                self.assertEqual(0, database.stat().st_size)


if __name__ == '__main__': unittest.main()
