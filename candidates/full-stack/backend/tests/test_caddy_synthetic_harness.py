"""Offline checks only; these do not execute Caddy or open sockets."""
from pathlib import Path
import socket
import threading
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from railway_candidate import caddy_synthetic_harness as harness


class FakeSocket:
    def __init__(self, reply):
        self.reply = reply
        self.writes = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def settimeout(self, value): pass
    def sendall(self, value): self.writes.append(value)
    def recv(self, size): return self.reply


class OfflineHarnessTests(TestCase):
    def fresh_storage(self):
        return harness.BUILD / ('caddy-synthetic-' + uuid4().hex)

    def test_config_is_fixed_loopback_and_scoped(self):
        config = harness.render_config(18081, 18082, self.fresh_storage())
        for item in ('http://127.0.0.1:18081', 'reverse_proxy 127.0.0.1:18082',
                     'bind 127.0.0.1', 'admin off', 'persist_config off',
                     'protocols h1', 'network_proxy none',
                     'read_header 2s', 'read_body 3s', 'write 3s'):
            self.assertIn(item, config)
        self.assertNotIn('__PROXY_PORT__', config)
        self.assertNotIn('__UPSTREAM_PORT__', config)
        self.assertNotIn('__STORAGE_PATH__', config)

    def test_reject_invalid_ports_and_storage(self):
        for port in (True, 0, 1023, 65536, '18081'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                harness.render_config(port, 18082, self.fresh_storage())
        with self.assertRaises(ValueError):
            harness.render_config(18081, 18081, self.fresh_storage())
        with self.assertRaises(ValueError):
            harness.render_config(18081, 18082, Path('relative'))
        with self.assertRaises(ValueError):
            harness.render_config(18081, 18082, harness.BASE / 'other')
        with self.assertRaises(ValueError):
            harness.render_config(18081, 18082, harness.BUILD)

    def test_config_write_rejects_other_directories_without_open(self):
        with patch('pathlib.Path.open') as opened, self.assertRaises(ValueError):
            harness.write_config_once(str(harness.BASE / 'elsewhere'), 'not written')
        opened.assert_not_called()

    def test_timeout_observation_is_not_normal_success(self):
        fake = FakeSocket(b'HTTP/1.1 408 Request Timeout\r\n\r\n')
        with patch.object(harness.socket, 'create_connection', return_value=fake), \
             patch.object(harness.time, 'monotonic', side_effect=[10, 12]):
            self.assertEqual(harness.closed_or_timeout_response(18081, b'partial'),
                             'closure-observed')
        fake = FakeSocket(b'HTTP/1.1 200 OK\r\n\r\n')
        with patch.object(harness.socket, 'create_connection', return_value=fake), \
             patch.object(harness.time, 'monotonic', side_effect=[10, 12]):
            self.assertEqual(harness.closed_or_timeout_response(18081, b'partial'),
                             'inconclusive')

    def test_immediate_failure_is_inconclusive(self):
        fake = FakeSocket(b'')
        with patch.object(harness.socket, 'create_connection', return_value=fake), \
             patch.object(harness.time, 'monotonic', side_effect=[10, 10.01]):
            self.assertEqual(harness.closed_or_timeout_response(18081, b'partial'),
                             'inconclusive')

    def test_fixture_wakes_only_tracked_sockets(self):
        class Request:
            def __init__(self): self.calls = []
            def shutdown(self, how): self.calls.append(('shutdown', how))
            def close(self): self.calls.append(('close',))
        owned, unrelated = Request(), Request()
        fixture = harness.Fixture.__new__(harness.Fixture)
        fixture._request_lock = threading.Lock()
        fixture._requests = {owned}
        fixture.close_owned_requests()
        self.assertEqual(owned.calls, [('shutdown', socket.SHUT_RDWR), ('close',)])
        self.assertEqual(unrelated.calls, [])

    def test_shutdown_reports_unfinished_close(self):
        release = threading.Event()
        class Server:
            def shutdown(self): pass
            def close_owned_requests(self): pass
            def server_close(self): release.wait(2)
        class ServeThread:
            def join(self, timeout): pass
            def is_alive(self): return False
        try:
            with self.assertRaisesRegex(RuntimeError, 'shutdown uncertain'):
                harness.bounded_fixture_shutdown(Server(), ServeThread(), seconds=0.01)
        finally:
            release.set()

    def test_shutdown_timeout_closes_only_owned_listener_and_reports_uncertain(self):
        release = threading.Event()
        class Listener:
            def __init__(self): self.closed = False
            def close(self): self.closed = True
        class Server:
            def __init__(self): self.socket = Listener()
            def shutdown(self): release.wait(2)
            def close_owned_requests(self): pass
            def server_close(self): pass
        class ServeThread:
            def join(self, timeout): pass
            def is_alive(self): return False
        fixture = Server()
        try:
            with self.assertRaisesRegex(RuntimeError, 'shutdown uncertain'):
                harness.bounded_fixture_shutdown(fixture, ServeThread(), seconds=0.01)
            self.assertTrue(fixture.socket.closed)
        finally:
            release.set()

