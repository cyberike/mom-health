"""Synthetic Android wire-shape integration: local TestClient/SQLite, no listener.

Values are fictional. This does not execute Android JSON, SQLite, TLS, or device code.
"""
import unittest
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from mom_health_backend.app import Settings, create_app


class AndroidWireContract(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).resolve().parents[2] / 'build' / 'contract-fixtures' / str(uuid4())
        folder.mkdir(parents=True)
        self.token = 'synthetic-contract-credential-never-deployed'
        self.client = TestClient(create_app(Settings(sqlite_path=str(folder / 'synthetic.sqlite3'),
                                                      allow_real_data=True, access_token=self.token)),
                                 client=('127.0.0.1', 50000))
        self.client.__enter__()
        self.auth = {'Authorization': 'Bearer ' + self.token}
        self.device, self.session = str(uuid4()), str(uuid4())

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def sample(self, **overrides):
        value = dict(sample_id=str(uuid4()), device_id=self.device, session_id=self.session,
                     model='V8', source='live', received_at='2026-10-26T07:12:20.123Z',
                     measured_at=None, heart_rate_bpm=74, missing_reason=None, synthetic=False)
        value.update(overrides)
        return value

    def post(self, samples, batch_id=None, headers=None):
        body = dict(schema_version=1, batch_id=batch_id or str(uuid4()), samples=samples)
        return self.client.post('/v1/batches', json=body, headers=headers or self.auth)

    def test_android_shape_and_acknowledgment_arrays(self):
        values = [self.sample(), self.sample(heart_rate_bpm=None, missing_reason='no_result')]
        batch_id = str(uuid4())
        res = self.post(values, batch_id)
        self.assertEqual(res.status_code, 200)
        ack = res.json()
        self.assertEqual(ack['batch_id'], batch_id)
        self.assertEqual(set(ack['accepted_ids']), {s['sample_id'] for s in values})
        self.assertEqual(ack['duplicate_ids'], [])
        self.assertEqual(ack['rejected'], [])
        page = self.client.get('/v1/samples', params={'device_id': self.device}, headers=self.auth)
        self.assertEqual(page.status_code, 200)
        self.assertEqual([s['heart_rate_bpm'] for s in page.json()['samples']], [74, None])

    def test_new_batch_id_retry_remains_idempotent(self):
        values = [self.sample()]
        self.assertEqual(self.post(values).json()['accepted_ids'], [values[0]['sample_id']])
        ack = self.post(values).json()
        self.assertEqual(ack['accepted_ids'], [])
        self.assertEqual(ack['duplicate_ids'], [values[0]['sample_id']])
        page = self.client.get('/v1/samples', params={'device_id': self.device}, headers=self.auth)
        self.assertEqual(len(page.json()['samples']), 1)

    def test_partial_collision_identifies_rejected_and_committed_ids(self):
        earlier = self.sample()
        self.post([earlier])
        collision = dict(earlier, heart_rate_bpm=75)
        fresh = self.sample()
        response = self.post([collision, fresh]).json()
        self.assertEqual(response['accepted_ids'], [fresh['sample_id']])
        self.assertEqual(response['rejected'], [{'sample_id': earlier['sample_id'], 'reason': 'sample_id_collision'}])

    def test_token_missing_rejects_batch_without_mutation(self):
        values = [self.sample()]
        body = dict(schema_version=1, batch_id=str(uuid4()), samples=values)
        self.assertEqual(self.client.post('/v1/batches', json=body).status_code, 401)
        page = self.client.get('/v1/samples', params={'device_id': self.device}, headers=self.auth)
        self.assertEqual(page.json()['samples'], [])

    def test_empty_batch_and_foreign_field_rejected(self):
        self.assertEqual(self.post([]).status_code, 422)
        self.assertEqual(self.post([self.sample(glucose=99)]).status_code, 422)


if __name__ == '__main__':
    unittest.main(verbosity=2)
