"""Independent, in-process synthetic tests for the separate direct-auth design.

No listener, device, real data or production credentials are used. These tests
do not prove Railway ingress identity, TLS, origin isolation or public readiness.
"""
import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from railway_candidate.direct_candidate import create_direct_candidate, FingerprintLimiter


HOST = 'synthetic.example.com'
USER = 'synthetic-caregiver'
PASSWORD = 'fictional-caregiver-password-for-tests-12345'
PHONE = 'fictional-phone-token-for-tests-1234567890'
OLD_USER = 'previous-caregiver'
OLD_PASSWORD = 'fictional-previous-password-for-tests-12345'
OLD_PHONE = 'fictional-previous-phone-token-tests-12345'


def basic(user, password):
    return 'Basic ' + base64.b64encode((user + ':' + password).encode('ascii')).decode('ascii')


def bearer(token):
    return 'Bearer ' + token


class DirectAuthAdversarialTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / 'build' / 'direct-adversarial' / str(uuid4())
        root.mkdir(parents=True)
        self.db = root / 'fixture.sqlite3'
        self.wall = 1000.0
        self.mono = 100.0
        with patch('railway_candidate.direct_candidate.validate_storage', return_value=str(self.db)):
            app = create_direct_candidate(
                HOST, USER, PASSWORD, PHONE, str(self.db), str(root),
                caregiver_previous=(OLD_USER, OLD_PASSWORD, 1100.0),
                phone_previous=(OLD_PHONE, 1100.0),
                clock=lambda: self.mono, wall_clock=lambda: self.wall,
            )
        self.app = app
        self.client = TestClient(app, client=('127.0.0.1', 1234))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def request(self, method, path, auth=None, **kwargs):
        headers = {'host': HOST}
        if auth is not None:
            headers['authorization'] = auth
        headers.update(kwargs.pop('headers', {}))
        return self.client.request(method, path, headers=headers, **kwargs)

    def test_routes_require_correct_separate_roles(self):
        caregiver = basic(USER, PASSWORD)
        phone = bearer(PHONE)
        for path in ('/dashboard/', '/dashboard/app.js', '/dashboard/core.js',
                     '/dashboard/styles.css', '/v1/health'):
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path).status_code, 401)
                self.assertEqual(self.request('GET', path, phone).status_code, 401)
                self.assertEqual(self.request('GET', path, caregiver).status_code, 200)
        self.assertEqual(self.request('POST', '/v1/batches', caregiver, json={}).status_code, 401)
        self.assertEqual(self.request('POST', '/v1/batches', phone, json={}).status_code, 422)
        self.assertEqual(self.request('GET', '/v1/gateway-status', phone).status_code, 401)
        self.assertEqual(self.request('POST', '/v1/gateway-status', caregiver, json={}).status_code, 401)
        self.assertEqual(self.request('GET', '/no-such-route', caregiver).status_code, 404)

    def test_forwarding_headers_cannot_grant_authorization(self):
        for values in (
            {'x-forwarded-for': '127.0.0.1', 'x-forwarded-proto': 'https'},
            {'x-real-ip': '127.0.0.1', 'x-forwarded-host': HOST},
            {'x-forwarded-user': USER, 'x-forwarded-email': USER},
        ):
            with self.subTest(values=values):
                self.assertEqual(self.request('GET', '/v1/health', headers=values).status_code, 401)
                self.assertEqual(self.request('POST', '/v1/batches', headers=values, json={}).status_code, 401)
        duplicates = [('host', HOST), ('x-forwarded-for', '127.0.0.1'),
                      ('x-forwarded-for', '192.0.2.10'), ('authorization', 'Bearer invalid')]
        self.assertEqual(self.client.get('/v1/health', headers=duplicates).status_code, 401)
        self.assertEqual(self.request('GET', '/v1/health', basic(USER, PASSWORD), headers={
            'x-forwarded-proto':'http', 'x-forwarded-host':'other.example.com',
            'x-forwarded-user':'untrusted-other-user'}).status_code, 200)

    def test_spoofed_forwarded_ips_do_not_reset_failure_budget(self):
        for index in range(8):
            response = self.request('GET', '/v1/health', 'Basic ZmFrZTpmYWtl',
                                    headers={'x-forwarded-for':'192.0.2.' + str(index + 1)})
            self.assertEqual(response.status_code, 401)
        response = self.request('GET', '/v1/health', 'Basic ZmFrZTpmYWtl',
                                headers={'x-forwarded-for':'198.51.100.99'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.request('GET', '/v1/health', basic(USER, PASSWORD)).status_code, 200)

    def test_ambiguous_authorization_and_host_rejected(self):
        for headers in (
            [('host', HOST), ('authorization', basic(USER, PASSWORD)), ('authorization', bearer(PHONE))],
            [('host', HOST), ('host', 'other.example.com'), ('authorization', basic(USER, PASSWORD))],
        ):
            with self.subTest(headers=len(headers)):
                response = self.client.get('/v1/health', headers=headers)
                self.assertEqual(response.status_code, 400)
        self.assertNotEqual(self.request('GET', '/v1/health', basic(USER, PASSWORD),
                                         headers={'host': 'other.example.com'}).status_code, 200)

    def test_conflicting_or_repeated_body_framing_rejected(self):
        auth = bearer(PHONE)
        cases = (
            [('host', HOST), ('authorization', auth), ('content-length', '0'), ('transfer-encoding', 'chunked')],
            [('host', HOST), ('authorization', auth), ('content-length', '0'), ('content-length', '0')],
        )
        for headers in cases:
            with self.subTest(headers=len(headers)):
                self.assertEqual(self.client.post('/v1/batches', headers=headers, content=b'').status_code, 400)

    def test_previous_credentials_expire_without_extending_current(self):
        old_caregiver = basic(OLD_USER, OLD_PASSWORD)
        old_phone = bearer(OLD_PHONE)
        self.assertEqual(self.request('GET', '/v1/health', old_caregiver).status_code, 200)
        self.assertEqual(self.request('POST', '/v1/batches', old_phone, json={}).status_code, 422)
        self.wall = 1100.0
        self.assertEqual(self.request('GET', '/v1/health', old_caregiver).status_code, 401)
        self.assertEqual(self.request('POST', '/v1/batches', old_phone, json={}).status_code, 401)
        self.assertEqual(self.request('GET', '/v1/health', basic(USER, PASSWORD)).status_code, 200)
        self.assertEqual(self.request('POST', '/v1/batches', bearer(PHONE), json={}).status_code, 422)

    def test_rotation_clock_rollback_does_not_revive_old_credentials(self):
        previous = basic(OLD_USER, OLD_PASSWORD)
        self.wall = 1100.0
        self.assertEqual(self.request('GET', '/v1/health', previous).status_code, 401)
        self.wall = 1099.0
        self.assertEqual(self.request('GET', '/v1/health', previous).status_code, 503)
        self.assertEqual(self.request('GET', '/v1/health', basic(USER, PASSWORD)).status_code, 503)
        self.wall = 1100.0
        self.assertEqual(self.request('GET', '/v1/health', previous).status_code, 401)
        self.assertEqual(self.request('GET', '/v1/health', basic(USER, PASSWORD)).status_code, 200)

    def test_path_encodings_and_method_changes_do_not_expand_routes(self):
        for path in ('/v1/%68ealth', '/v1%2Fhealth', '/v1/health/', '/v1//health',
                     '/dashboard/%61pp.js', '/dashboard//app.js'):
            with self.subTest(path=path):
                response = self.request('GET', path, basic(USER, PASSWORD))
                self.assertNotEqual(response.status_code, 200)
        for method, path, credential in (
            ('POST', '/v1/health', bearer(PHONE)),
            ('DELETE', '/v1/health', basic(USER, PASSWORD)),
            ('GET', '/v1/batches', basic(USER, PASSWORD)),
            ('HEAD', '/dashboard/', basic(USER, PASSWORD)),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, credential).status_code, 404)

    def test_repeated_invalid_auth_is_bounded_without_locking_valid_roles(self):
        for _ in range(8):
            self.assertEqual(self.request('GET', '/v1/health', 'Basic ZmFrZTpmYWtl').status_code, 401)
        blocked = self.request('GET', '/v1/health', 'Basic ZmFrZTpmYWtl')
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers['retry-after'], '60')
        self.assertEqual(self.request('GET', '/v1/health', basic(USER, PASSWORD)).status_code, 200)
        self.assertEqual(self.request('POST', '/v1/batches', bearer(PHONE), json={}).status_code, 422)
        self.mono += 60
        self.assertEqual(self.request('GET', '/v1/health', 'Basic ZmFrZTpmYWtl').status_code, 401)

    def test_anonymous_requests_do_not_suppress_browser_challenge(self):
        for _ in range(20):
            response = self.request('GET', '/dashboard/')
            self.assertEqual(response.status_code, 401)
            self.assertTrue(response.headers.get('www-authenticate', '').startswith('Basic '))
        self.assertEqual(self.request('GET', '/dashboard/', basic(USER, PASSWORD)).status_code, 200)

    def test_maximum_valid_bearer_and_oversize_header(self):
        long_token = 'x' * 4096
        with patch('railway_candidate.direct_candidate.validate_storage', return_value=str(self.db)):
            app = create_direct_candidate(HOST, USER, PASSWORD, long_token,
                                          str(self.db), str(self.db.parent),
                                          clock=lambda: self.mono, wall_clock=lambda: self.wall)
        with TestClient(app, client=('127.0.0.1', 1234)) as client:
            self.assertEqual(client.post('/v1/batches', headers={'host': HOST, 'authorization': bearer(long_token)}, json={}).status_code, 422)
            self.assertEqual(client.post('/v1/batches', headers={'host': HOST, 'authorization': bearer(long_token + 'x')}, json={}).status_code, 400)

    def test_previous_phone_upload_replays_after_current_token_rotation(self):
        sample = dict(sample_id=str(uuid4()), device_id=str(uuid4()), session_id=str(uuid4()),
                      model='V8', source='live', received_at='2026-10-28T10:00:00Z',
                      measured_at=None, heart_rate_bpm=70, missing_reason=None, synthetic=True)
        batch = {'schema_version': 1, 'batch_id': str(uuid4()), 'samples': [sample]}
        initial = self.request('POST', '/v1/batches', bearer(OLD_PHONE), json=batch)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()['accepted_ids'], [sample['sample_id']])
        self.wall = 1100.0
        self.assertEqual(self.request('POST', '/v1/batches', bearer(OLD_PHONE), json=batch).status_code, 401)
        retry = self.request('POST', '/v1/batches', bearer(PHONE), json=batch)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json(), initial.json())

    def test_limiter_concurrency_and_unique_input_memory_bound(self):
        limiter = FingerprintLimiter(clock=lambda: 100.0, limit=4, seconds=60, max_entries=5)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: limiter.record_invalid('phone', b'fictional-invalid'), range(100)))
        self.assertEqual(sum(result == 0 for result in results), 4)
        self.assertEqual(sum(result > 0 for result in results), 96)
        for index in range(100):
            limiter.record_invalid('phone', ('invalid-' + str(index)).encode('ascii'))
        self.assertLessEqual(len(limiter.entries), 5)
        self.assertTrue(all(isinstance(key, bytes) and len(key) == 32 for key in limiter.entries))

    def test_cancelled_callers_keep_slots_until_database_work_finishes(self):
        class BlockingSyntheticRepository:
            def __init__(self):
                self.started = 0
                self.lock = threading.Lock()
                self.all_started = threading.Event()
                self.release = threading.Event()

            def ingest(self, batch, allow_real):
                if allow_real:
                    raise AssertionError('real-data mode must remain disabled')
                with self.lock:
                    self.started += 1
                    if self.started == 32:
                        self.all_started.set()
                if not self.release.wait(timeout=10):
                    raise TimeoutError('test repository did not release')
                return {'batch_id': batch.batch_id, 'accepted_ids': [], 'duplicate_ids': [], 'rejected': []}

        repository = BlockingSyntheticRepository()
        with patch('railway_candidate.direct_candidate.validate_storage', return_value=str(self.db)):
            app = create_direct_candidate(HOST, USER, PASSWORD, PHONE, str(self.db), str(self.db.parent),
                                          clock=lambda: self.mono, wall_clock=lambda: self.wall,
                                          repository=repository)

        async def exercise():
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                     'method': 'POST', 'scheme': 'http', 'path': '/v1/batches',
                     'raw_path': b'/v1/batches', 'query_string': b'',
                     'headers': [(b'host', HOST.encode()),
                                 (b'content-type', b'application/json'),
                                 (b'authorization', bearer(PHONE).encode())],
                     'client': ('127.0.0.1', 1234), 'server': ('synthetic.example.com', 80)}
            sample = dict(sample_id=str(uuid4()), device_id=str(uuid4()), session_id=str(uuid4()),
                          model='V8', source='live', received_at='2026-10-28T10:00:00Z',
                          measured_at=None, heart_rate_bpm=70, missing_reason=None, synthetic=True)
            body = json.dumps({'schema_version': 1, 'batch_id': str(uuid4()), 'samples': [sample]}).encode()

            async def call():
                messages = []
                delivered = False
                async def receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {'type': 'http.request', 'body': body, 'more_body': False}
                    return {'type': 'http.disconnect'}
                async def send(message):
                    messages.append(message)
                await app(dict(scope), receive, send)
                return messages

            tasks = [asyncio.create_task(call()) for _ in range(32)]
            try:
                self.assertTrue(await asyncio.to_thread(repository.all_started.wait, 5))
                extra = await asyncio.wait_for(call(), 2)
                self.assertEqual(extra[0]['status'], 503)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                still_full = await asyncio.wait_for(call(), 2)
                self.assertEqual(still_full[0]['status'], 503)
            finally:
                repository.release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
            # Internal shielded work releases slots only after its worker returns.
            for _ in range(100):
                await asyncio.sleep(0.01)
                messages = await asyncio.wait_for(call(), 2)
                if messages and messages[0]['status'] == 200:
                    return
            self.fail('Completed database work did not release slots')

        asyncio.run(exercise())

    def test_absolute_body_deadline_limits_stalls_and_trickles(self):
        class SyntheticRepository:
            def __init__(self): self.calls = 0
            def ingest(self, batch, allow_real):
                self.calls += 1
                return {'batch_id': batch.batch_id, 'accepted_ids': [], 'duplicate_ids': [], 'rejected': []}

        repository = SyntheticRepository()
        with patch('railway_candidate.direct_candidate.validate_storage', return_value=str(self.db)):
            app = create_direct_candidate(HOST, USER, PASSWORD, PHONE, str(self.db), str(self.db.parent),
                                          clock=lambda: self.mono, wall_clock=lambda: self.wall,
                                          repository=repository)
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'POST', 'scheme': 'http', 'path': '/v1/batches',
                 'raw_path': b'/v1/batches', 'query_string': b'',
                 'headers': [(b'host', HOST.encode()), (b'content-type', b'application/json'),
                             (b'authorization', bearer(PHONE).encode())],
                 'client': ('127.0.0.1', 1234), 'server': ('synthetic.example.com', 80)}

        async def exercise():
            never = asyncio.Event()
            async def stall():
                await never.wait()
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            async def trickle():
                await asyncio.sleep(0.01)
                return {'type': 'http.request', 'body': b' ', 'more_body': True}
            async def call(receive, messages=None):
                if messages is None:
                    messages = []
                async def send(message): messages.append(message)
                await app(dict(scope), receive, send)
                return messages
            pending_messages = [[] for _ in range(32)]
            tasks = [asyncio.create_task(call(stall if i % 2 else trickle, pending_messages[i])) for i in range(32)]
            await asyncio.sleep(0.02)
            self.assertTrue(all(not task.done() for task in tasks))
            self.assertTrue(all(not messages for messages in pending_messages))
            responses = await asyncio.wait_for(asyncio.gather(*tasks), 2)
            self.assertTrue(all(response and response[0]['status'] == 408 for response in responses))
            self.assertEqual(repository.calls, 0)
            sample = dict(sample_id=str(uuid4()), device_id=str(uuid4()), session_id=str(uuid4()),
                          model='V8', source='live', received_at='2026-10-28T10:00:00Z',
                          measured_at=None, heart_rate_bpm=70, missing_reason=None, synthetic=True)
            body = json.dumps({'schema_version': 1, 'batch_id': str(uuid4()), 'samples': [sample]}).encode()
            delivered = False
            async def complete():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                return {'type': 'http.disconnect'}
            response = await asyncio.wait_for(call(complete), 2)
            self.assertEqual(response[0]['status'], 200)
            self.assertEqual(repository.calls, 1)

        with patch('railway_candidate.direct_candidate.BODY_READ_SECONDS', 0.2):
            asyncio.run(exercise())

    def test_body_failures_do_not_reach_storage_and_get_shell_is_timed(self):
        class SyntheticRepository:
            def __init__(self): self.calls = 0
            def ingest(self, batch, allow_real):
                self.calls += 1
                raise AssertionError('invalid body must not reach repository')
        repository = SyntheticRepository()
        with patch('railway_candidate.direct_candidate.validate_storage', return_value=str(self.db)):
            app = create_direct_candidate(HOST, USER, PASSWORD, PHONE, str(self.db), str(self.db.parent),
                                          clock=lambda: self.mono, wall_clock=lambda: self.wall,
                                          repository=repository)
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'POST', 'scheme': 'http', 'path': '/v1/batches',
                 'raw_path': b'/v1/batches', 'query_string': b'',
                 'headers': [(b'host', HOST.encode()), (b'content-type', b'application/json'),
                             (b'authorization', bearer(PHONE).encode())],
                 'client': ('127.0.0.1', 1234), 'server': ('synthetic.example.com', 80)}

        async def exercise():
            async def call(request_scope, receive):
                messages = []
                async def send(message): messages.append(message)
                await app(request_scope, receive, send)
                return messages
            pieces = iter(({'type': 'http.request', 'body': b'x' * 65536, 'more_body': True},
                           {'type': 'http.request', 'body': b'x' * 65537, 'more_body': False}))
            async def oversized(): return next(pieces)
            response = await asyncio.wait_for(call(dict(scope), oversized), 2)
            self.assertEqual(response[0]['status'], 413)
            async def disconnected(): return {'type': 'http.disconnect'}
            self.assertEqual(await asyncio.wait_for(call(dict(scope), disconnected), 2), [])
            async def bad_frame(): return {'type': 'http.request', 'body': 'not bytes', 'more_body': False}
            self.assertEqual((await asyncio.wait_for(call(dict(scope), bad_frame), 2))[0]['status'], 400)
            shell = dict(scope, method='GET', path='/dashboard/', raw_path=b'/dashboard/',
                         headers=[(b'host', HOST.encode()), (b'authorization', basic(USER, PASSWORD).encode())])
            never = asyncio.Event()
            async def stall():
                await never.wait()
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            self.assertEqual((await asyncio.wait_for(call(shell, stall), 2))[0]['status'], 408)
            self.assertEqual(repository.calls, 0)

        with patch('railway_candidate.direct_candidate.BODY_READ_SECONDS', 0.05):
            asyncio.run(exercise())

    def test_invalid_rotation_configuration_fails_before_storage(self):
        unsafe = (
            {'caregiver_previous': (USER, PASSWORD, 1100.0)},
            {'phone_previous': (PHONE, 1100.0)},
            {'caregiver_previous': (OLD_USER, OLD_PASSWORD, 1000.0 + 8 * 86400)},
            {'phone_previous': (OLD_PHONE, float('nan'))},
            {'phone_previous': (PASSWORD, 1100.0)},
            {'caregiver_previous': ('bad:user', OLD_PASSWORD, 1100.0)},
        )
        for value in unsafe:
            with self.subTest(value=repr(value)):
                with patch('railway_candidate.direct_candidate.validate_storage') as storage:
                    with self.assertRaises(ValueError):
                        create_direct_candidate(HOST, USER, PASSWORD, PHONE, 'ignored', 'ignored',
                                                wall_clock=lambda: 1000.0, **value)
                    storage.assert_not_called()

    def test_real_marked_sample_is_rejected_without_persistence(self):
        sample = dict(sample_id=str(uuid4()), device_id=str(uuid4()), session_id=str(uuid4()),
                      model='V8', source='live', received_at='2026-10-28T10:00:00Z',
                      measured_at=None, heart_rate_bpm=70, missing_reason=None, synthetic=False)
        batch = {'schema_version': 1, 'batch_id': str(uuid4()), 'samples': [sample]}
        response = self.request('POST', '/v1/batches', bearer(PHONE), json=batch)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rejected'][0]['reason'], 'real_data_disabled')
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM samples').fetchone(), (0,))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM batches').fetchone(), (0,))


if __name__ == '__main__':
    unittest.main()
