"""The real-browser checks' backend answers as quickly as the documented server.

tests/browser_backend.py serves Uvicorn on a listener it binds itself. Unless that listener is an IPPROTO_TCP
socket, asyncio leaves TCP_NODELAY off, and every response after a connection's first then reaches the client
about 40 ms after its headers (Nagle's algorithm waiting for the client's delayed ACK). The documented server
(``python -m mom_health_backend``) answers within a few milliseconds, so the browser checks would see a slower
server than the one they stand for. One check times the responses; the other pins the listener itself, which no
load on the machine can blur. Loopback only, fictional token, a fresh fixture under build/.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest

HERE = Path(__file__).resolve().parent
TOKEN = 'synthetic-browser-token-not-for-deployment-0001'  # the test constant browser_backend.py serves


def read_response(connection, pending=b''):
    """One HTTP/1.1 response with a Content-Length: (seconds from its headers to the end of its body, the rest)."""
    data = pending
    while b'\r\n\r\n' not in data:
        chunk = connection.recv(65536)
        if not chunk:
            raise ConnectionError('closed before the headers')
        data += chunk
    headed = time.monotonic()
    head, body = data.split(b'\r\n\r\n', 1)
    length = next(int(line.split(b':', 1)[1]) for line in head.split(b'\r\n') if line.lower().startswith(b'content-length:'))
    while len(body) < length:
        chunk = connection.recv(65536)
        if not chunk:
            raise ConnectionError('closed before the body')
        body += chunk
    return time.monotonic() - headed, body[length:]


class BrowserBackendTimingTests(unittest.TestCase):
    def test_a_keep_alive_response_body_does_not_wait_for_the_clients_ack(self):
        backend = subprocess.Popen([sys.executable, str(HERE / 'browser_backend.py')], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, env={name: value for name, value in os.environ.items() if not name.startswith('PG')})
        self.addCleanup(backend.wait, 30)
        self.addCleanup(backend.stdin.close)  # it stops once stdin closes
        self.addCleanup(backend.stdout.close)
        port = json.loads(backend.stdout.readline())['port']
        request = ('GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer %s\r\n\r\n' % (port, TOKEN)).encode()
        with socket.create_connection(('127.0.0.1', port), timeout=30) as connection:
            delays, rest = [], b''
            for _ in range(8):
                connection.sendall(request)
                delay, rest = read_response(connection, rest)
                delays.append(delay)
        # The first response on a connection is quick either way; with Nagle's algorithm on, every later one waits
        # about 40 ms for the client's delayed ACK. The median of seven keeps a moderately busy machine from deciding
        # this. A client held up past its delayed-ACK time sees even a held-back body arrive with its headers, though,
        # so one starved before most responses (stopped about 100 ms at a time, say) can still let the regression pass:
        # the check below pins the listener itself.
        self.assertLess(sorted(delays[1:])[3], 0.02, 'response bodies trailed their headers by (s): %s' % ['%.4f' % d for d in delays])

    def test_the_listener_is_an_ipproto_tcp_socket_on_loopback(self):
        spec = importlib.util.spec_from_file_location('browser_backend', HERE / 'browser_backend.py')
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        listener = backend.loopback_listener(0)
        self.addCleanup(listener.close)
        self.assertEqual(socket.IPPROTO_TCP, listener.proto, 'asyncio sets TCP_NODELAY only on an IPPROTO_TCP socket')
        self.assertEqual('127.0.0.1', listener.getsockname()[0])


if __name__ == '__main__':
    unittest.main()
