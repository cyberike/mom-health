"""Actual local TCP checks for the isolated synthetic Uvicorn candidate.

No Railway, credentials, health records, phone, or external network is used.
These tests do not establish a production ingress timeout or abuse defense.
"""
import base64
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import uvicorn

from railway_candidate.direct_candidate import create_direct_candidate
from railway_candidate.direct_server_config import build_local_config


HOST = 'synthetic.example.com'
USER = 'fictional-caregiver'
PASSWORD = 'fictional-caregiver-password-for-tests-12345'
PHONE = 'fictional-phone-token-for-tests-1234567890'
BASIC = 'Basic ' + base64.b64encode((USER + ':' + PASSWORD).encode()).decode()


class BlockingSyntheticRepository:
    def __init__(self):
        self.count = 0
        self.lock = threading.Lock()
        self.full = threading.Event()
        self.release = threading.Event()

    def initialize(self):
        pass

    def ingest(self, batch, allow_real=False):
        if allow_real:
            raise AssertionError('Synthetic-only')
        with self.lock:
            self.count += 1
            if self.count == 32:
                self.full.set()
        if not self.release.wait(12):
            raise TimeoutError('Synthetic worker timed out')
        return {'batch_id': batch.batch_id, 'accepted_ids': [], 'duplicate_ids': [], 'rejected': []}


class RealLoopbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = BlockingSyntheticRepository()
        cls.storage_patch = patch('railway_candidate.direct_candidate.validate_storage', return_value='synthetic-only-unused.sqlite3')
        cls.storage_patch.start()
        cls.body_patch = patch('railway_candidate.direct_candidate.BODY_READ_SECONDS', 0.3)
        cls.body_patch.start()
        app = create_direct_candidate(HOST, USER, PASSWORD, PHONE, 'unused', 'unused', repository=cls.repo)
        cls.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cls.listener.bind(('127.0.0.1', 0))
        cls.listener.listen(128)
        cls.port = cls.listener.getsockname()[1]
        cls.server = uvicorn.Server(build_local_config(app, port=cls.port))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={'sockets': [cls.listener]}, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 8
        while not cls.server.started and cls.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not cls.server.started:
            cls.repo.release.set()
            cls.server.should_exit = True
            cls.thread.join(5)
            cls.body_patch.stop()
            cls.storage_patch.stop()
            raise RuntimeError('Isolated loopback server failed to start')

    @classmethod
    def tearDownClass(cls):
        cls.repo.release.set()
        cls.server.should_exit = True
        cls.thread.join(8)
        cls.listener.close()
        cls.body_patch.stop()
        cls.storage_patch.stop()
        if cls.thread.is_alive():
            raise RuntimeError('Isolated loopback server failed to stop')

    def connect(self):
        stream = socket.create_connection(('127.0.0.1', self.port), timeout=2)
        stream.settimeout(2)
        self.addCleanup(stream.close)
        return stream

    def health(self):
        stream = self.connect()
        stream.sendall(('GET /v1/health HTTP/1.1\r\nHost: ' + HOST + '\r\nAuthorization: ' + BASIC + '\r\nConnection: close\r\n\r\n').encode())
        return stream.recv(4096)

    def test_01_normal_auth_and_http_body_deadline(self):
        self.assertIn(b' 200 ', self.health().split(b'\r\n', 1)[0])
        stream = self.connect()
        stream.sendall(('POST /v1/batches HTTP/1.1\r\nHost: ' + HOST + '\r\nAuthorization: Bearer ' + PHONE + '\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{').encode())
        started = time.monotonic()
        response = stream.recv(4096)
        self.assertIn(b' 408 ', response.split(b'\r\n', 1)[0])
        self.assertGreaterEqual(time.monotonic() - started, 0.15)
        self.assertEqual(self.repo.count, 0)

    def test_02_first_incomplete_headers_have_no_keepalive_deadline(self):
        stream = self.connect()
        stream.sendall(('GET /v1/health HTTP/1.1\r\nHost: ' + HOST + '\r\n').encode())
        # Uvicorn's reviewed keep-alive timeout is 5 s; it does not govern the
        # first incomplete request header. A timeout here is evidence of an
        # unresolved gap, not of a public deployment's behavior.
        stream.settimeout(5.4)
        with self.assertRaises(socket.timeout):
            stream.recv(1)
        stream.sendall(('Authorization: ' + BASIC + '\r\nConnection: close\r\n\r\n').encode())
        stream.settimeout(2)
        self.assertIn(b' 200 ', stream.recv(4096).split(b'\r\n', 1)[0])

    def test_03_database_workers_stay_bounded_after_disconnect(self):
        sample = dict(sample_id=str(uuid4()), device_id=str(uuid4()), session_id=str(uuid4()),
                      model='V8', source='live', received_at='2026-10-28T10:00:00Z',
                      measured_at=None, heart_rate_bpm=70, missing_reason=None, synthetic=True)
        payload = json.dumps({'schema_version': 1, 'batch_id': str(uuid4()), 'samples': [sample]}).encode()
        request = ('POST /v1/batches HTTP/1.1\r\nHost: ' + HOST + '\r\nAuthorization: Bearer ' + PHONE +
                   '\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)) +
                   '\r\nConnection: close\r\n\r\n').encode() + payload
        clients = []
        try:
            for _ in range(32):
                stream = self.connect()
                clients.append(stream)
                stream.sendall(request)
            self.assertTrue(self.repo.full.wait(7), 'Synthetic workers did not all start')
            self.assertIn(b' 503 ', self.health().split(b'\r\n', 1)[0])
            clients[0].close()
            self.assertIn(b' 503 ', self.health().split(b'\r\n', 1)[0])
        finally:
            self.repo.release.set()
            for stream in clients:
                stream.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if b' 200 ' in self.health().split(b'\r\n', 1)[0]:
                return
            time.sleep(0.05)
        self.fail('Completed synthetic workers did not release admission slots')

    def test_04_uvicorn_concurrency_gate_uses_live_connections(self):
        # Uvicorn checks its connection count only after a complete request
        # header has arrived. Leave 63 loopback connections incomplete and
        # verify the next complete request receives its server-level 503.
        self.assertEqual(self.server.config.limit_concurrency, 64)
        clients = []
        try:
            for _ in range(63):
                stream = self.connect()
                clients.append(stream)
                stream.sendall(b'GET /v1/health HTTP/1.1\r\n')
            deadline = time.monotonic() + 4
            while len(self.server.server_state.connections) < 63 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(len(self.server.server_state.connections), 63)
            self.assertIn(b' 503 ', self.health().split(b'\r\n', 1)[0])
        finally:
            for stream in clients:
                stream.close()


if __name__ == '__main__':
    unittest.main()
