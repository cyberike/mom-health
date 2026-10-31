"""Cross-component synthetic launcher checks; no listener or device access.

Private storage validation is replaced by a narrow workspace-fixture stub on
Windows. These tests therefore cannot verify actual POSIX ownership or ACLs.
"""
import base64
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from railway_candidate.gateway import application

HOST = 'synthetic.example.com'
USER = 'synthetic-caregiver'
PASSWORD = 'fictional-caregiver-password-not-for-use-12345'
PHONE = 'fictional-phone-upload-token-not-for-use-67890'
BASIC = 'Basic ' + base64.b64encode((USER + ':' + PASSWORD).encode()).decode()
HEADERS = {'host': HOST, 'x-forwarded-proto': 'https'}


class SyntheticLauncherIntegration(unittest.TestCase):
    def setUp(self):
        self.mount = (Path(__file__).resolve().parents[2] / 'build' /
                      'launcher-integration-fixtures' / str(uuid4())).resolve()
        self.child = self.mount / 'mom-health-private'
        self.child.mkdir(parents=True)
        self.database = self.child / 'mom-health-synthetic.sqlite3'
        self.environment = {
            'MOM_HEALTH_ALLOW_REAL_DATA': 'false', 'MOM_HEALTH_PRIVATE_CHILD': 'true',
            'MOM_HEALTH_HOSTNAME': HOST, 'MOM_HEALTH_CAREGIVER_USER': USER,
            'MOM_HEALTH_CAREGIVER_PASSWORD': PASSWORD, 'MOM_HEALTH_UPLOAD_TOKEN': PHONE,
            'MOM_HEALTH_SYNTHETIC_DB_PATH': str(self.database),
            'MOM_HEALTH_VOLUME_MOUNT': str(self.mount),
            'MOM_HEALTH_TRUSTED_PROXY_CIDRS': '127.0.0.1/32',
        }
        self.storage_calls = []

    def fixture_storage(self, mount, database, *, require_new=False):
        self.assertEqual((mount, database, require_new), (str(self.mount), str(self.database), False))
        self.storage_calls.append((mount, database))
        return database

    def client(self):
        with patch.dict(os.environ, self.environment, clear=True), patch(
                'railway_candidate.gateway.validate_private_child_storage', side_effect=self.fixture_storage):
            app = application()
        return TestClient(app, client=('127.0.0.1', 1234))

    def sample(self, synthetic=True):
        return dict(sample_id=str(uuid4()), device_id=str(uuid4()), session_id=str(uuid4()),
                    model='V8', source='live', received_at='2026-01-01T00:00:00Z',
                    measured_at=None, heart_rate_bpm=70, missing_reason=None, synthetic=synthetic)

    def test_launcher_restart_and_atomic_mixed_rejection(self):
        saved = self.sample()
        first_batch = dict(schema_version=1, batch_id=str(uuid4()), samples=[saved])
        with self.client() as client:
            self.assertEqual(client.get('/dashboard/', headers=HEADERS).status_code, 401)
            health = client.get('/v1/health', headers={**HEADERS, 'authorization': BASIC})
            self.assertEqual(health.status_code, 200)
            self.assertIs(health.json()['synthetic_only'], True)
            accepted = client.post('/v1/batches', headers={**HEADERS, 'authorization': 'Bearer ' + PHONE}, json=first_batch)
            self.assertEqual(accepted.json()['accepted_ids'], [saved['sample_id']])

        real_marked = self.sample(False)
        other_synthetic = self.sample()
        mixed = dict(schema_version=1, batch_id=str(uuid4()), samples=[real_marked, other_synthetic])
        with self.client() as client:
            response = client.post('/v1/batches', headers={**HEADERS, 'authorization': 'Bearer ' + PHONE}, json=mixed)
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body['accepted_ids'], [])
            self.assertEqual({x['reason'] for x in body['rejected']}, {'real_data_disabled', 'batch_contains_real_data'})
            samples = client.get('/v1/samples?device_id=' + saved['device_id'],
                                 headers={**HEADERS, 'authorization': BASIC}).json()['samples']
            self.assertEqual([sample['sample_id'] for sample in samples], [saved['sample_id']])

        self.assertEqual(len(self.storage_calls), 2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM samples').fetchone(), (1,))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM batches').fetchone(), (1,))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM batches WHERE batch_id=?', (mixed['batch_id'],)).fetchone(), (0,))


if __name__ == '__main__':
    unittest.main()
