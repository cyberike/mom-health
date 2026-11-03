"""Client cancellation against the unmodified main app over real loopback TCP.

A real Uvicorn server on 127.0.0.1 serves create_app() with a real SQLite
repository whose connection can pause inside the ingest transaction on a test
gate, so a client can disconnect at a known point. Fictional data only.
"""
import asyncio
from contextlib import closing
import json
import socket
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from mom_health_backend.app import Settings, create_app
from mom_health_backend.repository import SQLiteRepository
from railway_candidate.direct_candidate import MAX_IN_FLIGHT, create_direct_candidate

try:
    from synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample
except ImportError:  # imported as tests.<module>
    from tests.synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample


class PausingConnection(sqlite3.Connection):
    """Pauses after the first sample INSERT of a gated ingest, inside its transaction."""
    gate = None

    def execute(self, sql, *args):
        result = super().execute(sql, *args)
        gate = PausingConnection.gate
        if gate is not None and sql.startswith('INSERT INTO samples'):
            PausingConnection.gate = None
            gate['entered'].set()
            if not gate['release'].wait(15):
                raise TimeoutError('synthetic test gate was never released')
        return result


class CancellationLoopbackTests(unittest.TestCase):
    def setUp(self):
        self.database = fixture_dir('cancellation-fixtures') / 'synthetic.sqlite3'
        repository = SQLiteRepository(self.database)
        repository.connect = lambda: sqlite3.connect(repository.path, timeout=5, factory=PausingConnection)
        self.app = create_app(Settings(sqlite_path=str(self.database), access_token=TOKEN), repository=repository)
        self.addCleanup(setattr, PausingConnection, 'gate', None)

    def raw(self, server, payload, *, send_bytes=None):
        stream = socket.create_connection(('127.0.0.1', server.port), timeout=5)
        head = ('POST /v1/batches HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer ' + TOKEN +
                '\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)) + '\r\n\r\n').encode()
        stream.sendall(head + payload[:send_bytes if send_bytes is not None else len(payload)])
        return stream

    def stored(self, sql):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(sql).fetchone()[0]

    def test_disconnect_during_body_upload_stores_nothing_and_server_stays_healthy(self):
        submitted = batch(*[sample() for _ in range(5)])
        payload = json.dumps(submitted).encode()
        with LoopbackServer(self.app) as server:
            stream = self.raw(server, payload, send_bytes=len(payload) // 2)
            time.sleep(0.2)
            stream.close()
            time.sleep(0.3)
            self.assertEqual(200, server.request('GET', '/v1/health')[0])
            self.assertEqual([], json.loads(server.request('GET', '/v1/devices')[1])['devices'])
            code, body = server.request('POST', '/v1/batches', submitted)
            self.assertEqual((200, 5), (code, len(json.loads(body)['accepted_ids'])))
        self.assertEqual(5, self.stored('SELECT COUNT(*) FROM samples'))
        self.assertEqual(1, self.stored('SELECT COUNT(*) FROM batches'))

    def test_client_that_gives_up_mid_ingest_still_gets_one_commit_and_its_receipt_on_retry(self):
        submitted = batch(*[sample() for _ in range(5)])
        gate = {'entered': threading.Event(), 'release': threading.Event()}
        PausingConnection.gate = gate
        with LoopbackServer(self.app) as server:
            stream = self.raw(server, json.dumps(submitted).encode())
            self.assertTrue(gate['entered'].wait(10), 'ingest never reached the gated INSERT')
            stream.close()  # the phone gives up while the server is inside the transaction
            # An identical retry arrives while the abandoned attempt still holds
            # the write lock; it must wait and then receive that attempt's receipt.
            retry = {}
            worker = threading.Thread(target=lambda: retry.update(result=server.request('POST', '/v1/batches', submitted)))
            worker.start()
            time.sleep(0.3)
            self.assertNotIn('result', retry)
            gate['release'].set()
            worker.join(15)
            code, body = retry['result']
            receipt = json.loads(body)
            self.assertEqual(200, code)
            self.assertEqual([s['sample_id'] for s in submitted['samples']], receipt['accepted_ids'])
            self.assertEqual([], receipt['duplicate_ids'] + receipt['rejected'])
            self.assertEqual(receipt, json.loads(server.request('POST', '/v1/batches', submitted)[1]))
        self.assertEqual(5, self.stored('SELECT COUNT(*) FROM samples'))
        self.assertEqual(1, self.stored('SELECT COUNT(*) FROM batches'))


class GateRepository:
    """Synthetic in-memory stand-in: counts entries, blocks on a gate, may fail."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reached, self.release = threading.Event(), threading.Event()
        self.entered = self.finished = self.target = 0
        self.fail = False

    def initialize(self):
        pass

    def arm(self, target, *, fail=False, blocking=True):
        with self.lock:
            self.entered = self.finished = 0
            self.target = target
        self.reached.clear()
        self.fail = fail
        (self.release.clear if blocking else self.release.set)()

    def ingest(self, batch, allow_real=False):
        if allow_real:
            raise AssertionError('synthetic-only')
        with self.lock:
            self.entered += 1
            if self.entered >= self.target:
                self.reached.set()
        if not self.release.wait(15):
            raise TimeoutError('synthetic test gate was never released')
        with self.lock:
            self.finished += 1
        if self.fail:
            raise RuntimeError('synthetic storage failure')
        return {'batch_id': batch.batch_id, 'accepted_ids': [], 'duplicate_ids': [], 'rejected': []}


class DirectCandidateSlotTests(unittest.TestCase):
    """In-process ASGI calls to the inactive direct candidate (fictional credentials)."""
    HOST = 'synthetic.example.com'
    PHONE = 'fictional-phone-token-for-slot-tests-0123456789'

    def test_admission_capacity_is_exactly_restored_after_failures_and_cancellations(self):
        repository = GateRepository()
        with patch('railway_candidate.direct_candidate.validate_storage', return_value='synthetic-only-unused.sqlite3'):
            app = create_direct_candidate(self.HOST, 'fictional-caregiver', 'fictional-caregiver-password-for-slot-tests-1',
                                          self.PHONE, 'unused', 'unused', repository=repository)
        body = json.dumps(batch(sample())).encode()
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
                 'path': '/v1/batches', 'raw_path': b'/v1/batches', 'query_string': b'',
                 'headers': [(b'host', self.HOST.encode()), (b'content-type', b'application/json'),
                             (b'authorization', ('Bearer ' + self.PHONE).encode())],
                 'client': ('127.0.0.1', 1234), 'server': (self.HOST, 80)}

        async def upload(broken_client=False):
            delivered, statuses = False, []

            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                return {'type': 'http.disconnect'}

            async def send(message):
                if broken_client:
                    raise OSError('synthetic client went away')
                if message['type'] == 'http.response.start':
                    statuses.append(message['status'])
            try:
                await app(dict(scope), receive, send)
            except OSError:
                return 'client-error'
            return statuses[0]

        async def capacity_is_exactly_restored():
            repository.arm(MAX_IN_FLIGHT)
            admitted = [asyncio.create_task(upload()) for _ in range(MAX_IN_FLIGHT)]
            try:
                self.assertTrue(await asyncio.to_thread(repository.reached.wait, 10),
                                'fewer than %d uploads reached storage' % MAX_IN_FLIGHT)
                self.assertEqual(503, await asyncio.wait_for(upload(), 5))
            finally:
                repository.release.set()
            self.assertEqual([200] * MAX_IN_FLIGHT, await asyncio.gather(*admitted))

        async def settle(expected):
            for _ in range(500):
                if repository.finished >= expected:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.1)  # let shielded tasks finish and release

        async def exercise():
            await capacity_is_exactly_restored()
            # Storage failures are answered 503 and release their slots.
            repository.arm(10 ** 9, fail=True, blocking=False)
            self.assertEqual({503}, set(await asyncio.gather(*[upload() for _ in range(3 * MAX_IN_FLIGHT)])))
            await capacity_is_exactly_restored()
            # A downstream send that raises (the client vanished) releases its slot.
            repository.arm(10 ** 9, blocking=False)
            self.assertEqual({'client-error'}, set(await asyncio.gather(*[upload(broken_client=True) for _ in range(MAX_IN_FLIGHT)])))
            await capacity_is_exactly_restored()
            # Cancelled callers keep their slots until the shielded work ends, then release all of them.
            repository.arm(MAX_IN_FLIGHT)
            cancelled = [asyncio.create_task(upload()) for _ in range(MAX_IN_FLIGHT)]
            self.assertTrue(await asyncio.to_thread(repository.reached.wait, 10))
            for task in cancelled:
                task.cancel()
            await asyncio.gather(*cancelled, return_exceptions=True)
            self.assertEqual(503, await asyncio.wait_for(upload(), 5))
            repository.release.set()
            await settle(MAX_IN_FLIGHT)
            await capacity_is_exactly_restored()

        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
