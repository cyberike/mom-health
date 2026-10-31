"""Prepared synthetic tests. Not executed during the build-only pass."""
import json
import sqlite3
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError

from mom_health_backend.app import Settings, create_app
from mom_health_backend.models import Batch, GatewayStatus
from mom_health_backend.repository import BatchConflict, SQLiteRepository


class WorkspaceFixture:
    """Unique synthetic database directory, retained for inspection; no recursive cleanup."""
    def __init__(self):
        root = Path(__file__).resolve().parents[2] / "build" / "backend-fixtures"
        path = root / str(uuid4())
        path.mkdir(parents=True)
        self.name = str(path)

    def cleanup(self):
        pass  # Synthetic fixture only; preserve evidence rather than alter Windows Temp ACLs.


def sample(**changes):
    value = dict(sample_id=str(uuid4()), device_id="synthetic-watch", session_id=str(uuid4()), model="V8", source="live", received_at="2026-10-22T00:00:00Z", measured_at=None, heart_rate_bpm=80, missing_reason=None, synthetic=True)
    value.update(changes)
    return value


def batch(*samples):
    return dict(schema_version=1, batch_id=str(uuid4()), samples=list(samples))


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceFixture()
        self.path = Path(self.temp.name) / "synthetic.sqlite3"
        self.repo = SQLiteRepository(self.path)
        self.repo.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_accept_replay_duplicate_collision_and_batch_conflict(self):
        first = sample()
        request = Batch.model_validate(batch(first))
        result = self.repo.ingest(request)
        self.assertEqual([first["sample_id"]], result["accepted_ids"])
        self.assertEqual(result, self.repo.ingest(request))
        self.assertEqual([first["sample_id"]], self.repo.ingest(Batch.model_validate(batch(first)))["duplicate_ids"])
        collision = dict(first, heart_rate_bpm=81)
        self.assertEqual("sample_id_collision", self.repo.ingest(Batch.model_validate(batch(collision)))["rejected"][0]["reason"])
        with self.assertRaises(BatchConflict):
            self.repo.ingest(Batch.model_validate(dict(request.model_dump(), samples=[collision])))

    def test_partial_batch_replay_and_retry_preserve_committed_data(self):
        original = sample()
        self.repo.ingest(Batch.model_validate(batch(original)))
        collision = dict(original, heart_rate_bpm=81)
        fresh = sample(heart_rate_bpm=82)
        partial = Batch.model_validate(batch(collision, fresh))

        first = self.repo.ingest(partial)
        self.assertEqual([fresh['sample_id']], first['accepted_ids'])
        self.assertEqual([], first['duplicate_ids'])
        self.assertEqual([{'sample_id': original['sample_id'], 'reason': 'sample_id_collision'}], first['rejected'])
        # Retrying the same batch after a lost response returns the exact
        # committed receipt and does not insert the fresh item twice.
        self.assertEqual(first, self.repo.ingest(partial))
        # A new batch for the already committed item is acknowledged as a duplicate.
        duplicate = self.repo.ingest(Batch.model_validate(batch(fresh)))
        self.assertEqual([fresh['sample_id']], duplicate['duplicate_ids'])
        self.assertEqual([], duplicate['accepted_ids'])
        rows = self.repo.page('synthetic-watch', 10)['samples']
        self.assertEqual({original['sample_id']: 80, fresh['sample_id']: 82},
                         {row['sample_id']: row['heart_rate_bpm'] for row in rows})

    def test_policy_rejects_real_or_mixed_batch_before_storage(self):
        rejected = sample(synthetic=False, heart_rate_bpm=137)
        synthetic = sample()
        mixed = Batch.model_validate(batch(rejected, synthetic))
        whole = Batch.model_validate(batch(rejected))
        with patch.object(self.repo, 'connect', side_effect=AssertionError('storage must not be opened')):
            result = self.repo.ingest(mixed)
            self.assertEqual(result, self.repo.ingest(mixed))
            self.assertEqual('real_data_disabled', self.repo.ingest(whole)['rejected'][0]['reason'])
        self.assertEqual([], result['accepted_ids'])
        self.assertEqual([], result['duplicate_ids'])
        self.assertEqual({'batch_id': mixed.batch_id, 'accepted_ids': [], 'duplicate_ids': [],
                          'rejected': [{'sample_id': rejected['sample_id'], 'reason': 'real_data_disabled'},
                                       {'sample_id': synthetic['sample_id'], 'reason': 'batch_contains_real_data'}]}, result)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(0, connection.execute('SELECT count(*) FROM samples').fetchone()[0])
            self.assertEqual(0, connection.execute('SELECT count(*) FROM batches').fetchone()[0])

    def test_rejected_mixed_batch_does_not_change_pure_synthetic_idempotency(self):
        synthetic = sample()
        pure = Batch.model_validate(batch(synthetic))
        first = self.repo.ingest(pure)
        mixed = Batch.model_validate(batch(sample(synthetic=False), synthetic))
        self.repo.ingest(mixed)
        self.assertEqual(first, self.repo.ingest(pure))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(1, connection.execute('SELECT count(*) FROM samples').fetchone()[0])
            self.assertEqual(1, connection.execute('SELECT count(*) FROM batches').fetchone()[0])

    def test_synthetic_mode_cannot_read_previously_stored_real_data(self):
        self.repo.ingest(Batch.model_validate(batch(sample(synthetic=False))), allow_real=True)
        self.assertEqual([], self.repo.page("synthetic-watch", 10)["samples"])
        self.assertEqual(1, len(self.repo.page("synthetic-watch", 10, allow_real=True)["samples"]))

    def test_pagination_device_isolation(self):
        values = [sample() for _ in range(3)]
        self.repo.ingest(Batch.model_validate(batch(*values, sample(device_id="other-watch"))))
        first = self.repo.page("synthetic-watch", 2)
        second = self.repo.page("synthetic-watch", 2, first["next_cursor"])
        self.assertEqual(2, len(first["samples"]))
        self.assertEqual(1, len(second["samples"]))
        self.assertIsNone(second["next_cursor"])
        with self.assertRaises(ValueError):
            self.repo.page("other-watch", 2, first["next_cursor"])

    def test_equal_time_and_value_do_not_collapse_distinct_observations(self):
        first = sample()
        second = dict(first, sample_id=str(uuid4()))
        result = self.repo.ingest(Batch.model_validate(batch(first, second)))
        self.assertEqual(2, len(result["accepted_ids"]))
        self.assertEqual(2, len(self.repo.page("synthetic-watch", 10)["samples"]))
        replay = self.repo.ingest(Batch.model_validate(batch(first, second)))
        self.assertEqual(2, len(replay["duplicate_ids"]))
        self.assertEqual(2, len(self.repo.page("synthetic-watch", 10)["samples"]))

    def test_recent_pagination_is_device_and_order_scoped(self):
        values=[sample(heart_rate_bpm=value) for value in (70,71,72)]
        self.repo.ingest(Batch.model_validate(batch(*values)))
        first=self.repo.page("synthetic-watch",2,order="desc")
        self.assertEqual([72,71],[row["heart_rate_bpm"] for row in first["samples"]])
        last=self.repo.page("synthetic-watch",2,first["next_cursor"],order="desc")
        self.assertEqual([70],[row["heart_rate_bpm"] for row in last["samples"]])
        with self.assertRaises(ValueError):
            self.repo.page("synthetic-watch",2,first["next_cursor"],order="asc")

    def test_device_summary_filters_real_samples_and_pages(self):
        self.repo.ingest(Batch.model_validate(batch(sample(device_id="a"),sample(device_id="b"),sample(device_id="c",synthetic=False))),allow_real=True)
        first=self.repo.devices(1)
        self.assertEqual(["a"],[d["device_id"] for d in first["devices"]])
        second=self.repo.devices(1,first["next_cursor"])
        self.assertEqual(["b"],[d["device_id"] for d in second["devices"]])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(3,len(self.repo.devices(100,allow_real=True)["devices"]))
        self.assertEqual(1,second["devices"][0]["sample_count"])

    def test_failure_rolls_back_and_emits_no_receipt(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON batches BEGIN SELECT RAISE(ABORT, 'synthetic fault'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.repo.ingest(Batch.model_validate(batch(sample())))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(0, connection.execute("SELECT count(*) FROM samples").fetchone()[0])

    def test_whole_batch_validation_before_mutation(self):
        first = sample()
        with self.assertRaises(ValidationError):
            Batch.model_validate(batch(first, first))
        for changes in ({"heart_rate_bpm": True}, {"heart_rate_bpm": 251}, {"received_at": "2026-10-22T00:00:00"}, {"glucose": 90}, {"model": "unknown"}, {"heart_rate_bpm": None}, {"device_id": "AA:BB:CC:DD:EE:FF"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Batch.model_validate(batch(sample(**changes)))
        valid = Batch.model_validate(batch(sample(heart_rate_bpm=None, missing_reason="unsupported")))
        self.assertIsNone(valid.samples[0].heart_rate_bpm)

    def test_unverified_real_capabilities_rejected_but_nullable_allowed(self):
        for changes in ({"model": "V4"}, {"source": "history"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Batch.model_validate(batch(sample(synthetic=False, **changes)))
            Batch.model_validate(batch(sample(synthetic=False, heart_rate_bpm=None, missing_reason="unsupported", **changes)))
            Batch.model_validate(batch(sample(synthetic=True, **changes)))

    def test_java_fraction_equivalence_and_nanosecond_collision(self):
        first = sample(received_at="2026-10-22T00:00:00.123456789Z")
        self.repo.ingest(Batch.model_validate(batch(first)))
        offset = dict(first, received_at="2026-10-22T01:00:00.123456789+01:00")
        self.assertEqual([first["sample_id"]], self.repo.ingest(Batch.model_validate(batch(offset)))["duplicate_ids"])
        changed = dict(first, received_at="2026-10-22T00:00:00.123456788Z")
        self.assertEqual("sample_id_collision", self.repo.ingest(Batch.model_validate(batch(changed)))["rejected"][0]["reason"])
        a = Batch.model_validate(batch(sample(received_at="2026-10-22T00:00:00.120Z"))).samples[0]
        b = Batch.model_validate(batch(sample(received_at="2026-10-22T00:00:00.12Z"))).samples[0]
        self.assertEqual(a.received_at, b.received_at)
        for timestamp in ("0000-01-01T00:00:00Z", "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValidationError):
                Batch.model_validate(batch(sample(received_at=timestamp)))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceFixture()
        self.settings = Settings(sqlite_path=str(Path(self.temp.name) / "api.sqlite3"))

    def tearDown(self):
        self.temp.cleanup()

    def client(self, settings=None, host="127.0.0.1"):
        return TestClient(create_app(settings or self.settings), client=(host, 50000))

    def test_health_ingest_query(self):
        with self.client() as client:
            self.assertTrue(client.get("/v1/health").json()["synthetic_only"])
            self.assertEqual(200, client.post("/v1/batches", json=batch(sample())).status_code)
            self.assertEqual(1, len(client.get("/v1/samples", params={"device_id": "synthetic-watch"}).json()["samples"]))

    def test_synthetic_api_keeps_real_marked_batch_identifiers_out_of_storage(self):
        request = batch(sample(synthetic=False), sample())
        with self.client() as client:
            first = client.post('/v1/batches', json=request)
            second = client.post('/v1/batches', json=request)
            self.assertEqual(200, first.status_code)
            self.assertEqual(first.json(), second.json())
            self.assertEqual([], first.json()['accepted_ids'])
            self.assertEqual(2, len(first.json()['rejected']))
            self.assertEqual([], client.get('/v1/samples', params={'device_id':'synthetic-watch'}).json()['samples'])
        with closing(sqlite3.connect(self.settings.sqlite_path)) as connection:
            self.assertEqual(0, connection.execute('SELECT count(*) FROM batches').fetchone()[0])
            self.assertEqual(0, connection.execute('SELECT count(*) FROM samples').fetchone()[0])

    def test_partial_synthetic_receipt_replay_and_new_batch_duplicate(self):
        original = sample()
        collision = dict(original, heart_rate_bpm=81)
        fresh = sample(heart_rate_bpm=82)
        request = batch(collision, fresh)
        with self.client() as client:
            self.assertEqual(200, client.post('/v1/batches', json=batch(original)).status_code)
            response = client.post('/v1/batches', json=request)
            self.assertEqual(200, response.status_code)
            receipt = response.json()
            self.assertEqual([fresh['sample_id']], receipt['accepted_ids'])
            self.assertEqual([{'sample_id': original['sample_id'], 'reason': 'sample_id_collision'}], receipt['rejected'])
            self.assertEqual(receipt, client.post('/v1/batches', json=request).json())
            retry = client.post('/v1/batches', json=batch(fresh)).json()
            self.assertEqual([fresh['sample_id']], retry['duplicate_ids'])
            saved = client.get('/v1/samples', params={'device_id': 'synthetic-watch'}).json()['samples']
            self.assertEqual({original['sample_id']: 80, fresh['sample_id']: 82},
                             {row['sample_id']: row['heart_rate_bpm'] for row in saved})

    def test_dashboard_api_auth_and_shell_privacy(self):
        settings=Settings(sqlite_path=self.settings.sqlite_path,access_token="synthetic-test-fixture-not-a-secret")
        with self.client(settings) as client:
            self.assertEqual(401,client.get("/v1/devices").status_code)
            shell=client.get("/dashboard/")
            self.assertEqual(200,shell.status_code)
            self.assertIn("no-store",shell.headers["cache-control"])
            self.assertIn("frame-ancestors 'none'",shell.headers["content-security-policy"])
            for asset in ("styles.css","core.js","app.js"):
                self.assertEqual(200,client.get("/dashboard/"+asset).status_code)
            headers={"Authorization":"Bearer synthetic-test-fixture-not-a-secret"}
            client.post("/v1/batches",json=batch(sample()),headers=headers)
            result=client.get("/v1/devices",headers=headers)
            self.assertEqual(1,len(result.json()["devices"]))
            self.assertEqual(200,client.get("/v1/samples",params={"device_id":"synthetic-watch","order":"desc"},headers=headers).status_code)
            self.assertEqual(422,client.get("/v1/samples",params={"device_id":"synthetic-watch","order":"invalid"},headers=headers).status_code)
            self.assertEqual(404,client.get("/dashboard/secret.txt",headers=headers).status_code)

    def test_gateway_status_policy_bounds_and_status_only_device(self):
        status=dict(schema_version=1,device_id="synthetic-status-only",model="V8",reported_at="2026-10-24T12:00:00Z",last_observation_at=None,battery_percent=64,connection="disconnected",pending_samples=21,uploaded_samples=0,recording_enabled=False,synthetic=True)
        with self.client() as client:
            self.assertIsNone(client.get("/v1/gateway-status",params={"device_id":status["device_id"]}).json()["status"])
            self.assertEqual(403,client.post("/v1/gateway-status",json=dict(status,synthetic=False)).status_code)
            self.assertEqual(422,client.post("/v1/gateway-status",json=dict(status,battery_percent=101)).status_code)
            self.assertEqual(422,client.post("/v1/gateway-status",json=dict(status,pending_samples=-1)).status_code)
            response=client.post("/v1/gateway-status",json=status)
            self.assertEqual(200,response.status_code)
            self.assertTrue(response.json()["accepted"])
            saved=client.get("/v1/gateway-status",params={"device_id":status["device_id"]}).json()
            self.assertEqual(21,saved["status"]["pending_samples"])
            self.assertEqual("disconnected",saved["status"]["connection"])
            listed=client.get("/v1/devices").json()["devices"]
            self.assertEqual(1,len(listed));self.assertEqual(0,listed[0]["sample_count"]);self.assertIsNone(listed[0]["latest"])

    def test_real_gateway_status_hidden_in_synthetic_mode(self):
        repo=SQLiteRepository(self.settings.sqlite_path);repo.initialize()
        status=GatewayStatus.model_validate(dict(schema_version=1,device_id="synthetic-hidden-status",model="V8",reported_at="2026-10-24T12:00:00Z",last_observation_at=None,battery_percent=None,connection="error",pending_samples=0,uploaded_samples=0,recording_enabled=False,synthetic=False))
        repo.save_status(status,allow_real=True)
        self.assertIsNone(repo.status(status.device_id)["status"])
        self.assertEqual([],repo.devices()["devices"])
        self.assertIsNotNone(repo.status(status.device_id,allow_real=True)["status"])

    def test_non_loopback_denied(self):
        with self.client(host="192.0.2.10") as client:
            self.assertEqual(403, client.get("/v1/health").status_code)

    def test_hosted_mode_requires_explicit_token_and_valid_hostname(self):
        with self.assertRaises(ValueError):
            Settings(hosted_hostname="care.example.org")
        for name in ("localhost", "care.example.org:443", "https://care.example.org", "CARE.example.org", "a..example.org"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Settings(hosted_hostname=name, access_token="synthetic-test-fixture-not-a-secret")

    def test_hosted_proxy_origin_and_authorization(self):
        token="synthetic-test-fixture-not-a-secret"
        settings=Settings(sqlite_path=self.settings.sqlite_path, hosted_hostname="care.example.org", access_token=token)
        valid={"Host":"care.example.org", "X-Forwarded-Proto":"https"}
        with self.client(settings) as client:
            self.assertEqual(403,client.get("/dashboard/").status_code)
            self.assertEqual(403,client.get("/dashboard/",headers={**valid,"Host":"other.example.org"}).status_code)
            self.assertEqual(403,client.get("/dashboard/",headers={**valid,"X-Forwarded-Proto":"http"}).status_code)
            self.assertEqual(403,client.get("/dashboard/",headers={**valid,"X-Forwarded-Proto":"https,http"}).status_code)
            self.assertEqual(200,client.get("/dashboard/",headers=valid).status_code)
            self.assertEqual(401,client.get("/v1/health",headers=valid).status_code)
            authorized={**valid,"Authorization":"Bearer "+token}
            self.assertEqual(200,client.post("/v1/batches",headers=authorized,json=batch(sample())).status_code)
            self.assertEqual(1,len(client.get("/v1/samples",headers=authorized,params={"device_id":"synthetic-watch"}).json()["samples"]))
        with self.client(settings,host="192.0.2.10") as client:
            self.assertEqual(403,client.get("/v1/health",headers=authorized).status_code)

    def test_ambiguous_security_headers_are_rejected(self):
        token="synthetic-test-fixture-not-a-secret"
        settings=Settings(sqlite_path=self.settings.sqlite_path, hosted_hostname="care.example.org", access_token=token)
        with self.client(settings) as client:
            valid=[("Host","care.example.org"),("X-Forwarded-Proto","https"),("Authorization","Bearer "+token)]
            for field,value in (("Host","other.example.org"),("X-Forwarded-Proto","http"),("Authorization","Bearer invalid")):
                with self.subTest(field=field):
                    self.assertEqual(400,client.get("/v1/health",headers=valid+[(field,value)]).status_code)

    def test_size_bound_and_sanitized_validation(self):
        with self.client() as client:
            self.assertEqual(413, client.post("/v1/batches", content=b"x" * 131073).status_code)
            response = client.post("/v1/batches", json=batch(sample(glucose="private-marker")))
            self.assertEqual(422, response.status_code)
            self.assertNotIn("private-marker", response.text)

    def test_real_mode_requires_explicit_authorization_for_reads_too(self):
        with self.assertRaises(ValueError):
            Settings(allow_real_data=True)
        # Synthetic test fixture only, never a deployed credential.
        settings = Settings(sqlite_path=self.settings.sqlite_path, allow_real_data=True, access_token="synthetic-test-fixture-not-a-secret")
        with self.client(settings) as client:
            self.assertEqual(401, client.get("/v1/health").status_code)
            headers = {"Authorization": "Bearer synthetic-test-fixture-not-a-secret"}
            self.assertEqual(200, client.post("/v1/batches", headers=headers, json=batch(sample())).status_code)

    def test_invalid_token_shapes_rejected(self):
        for token in ("x" * 31, "x" * 4097, "x" * 32 + "\r\n", "x" * 32 + " ", "x" * 32 + "é"):
            with self.subTest(length=len(token)), self.assertRaises(ValueError):
                Settings(access_token=token)


if __name__ == "__main__":
    unittest.main()
