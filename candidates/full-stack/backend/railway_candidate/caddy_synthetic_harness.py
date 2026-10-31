"""Source-only local Caddy experiment. This module never launches Caddy.

Importing it opens no sockets. Explicit --run starts a disposable HTTP/1 fixture
bound only to 127.0.0.1 and probes an already-running local proxy; it is not a
test of the Mom Health app, Railway, HTTPS, edge identity or device behavior.
Do not execute --run or start Caddy before the separate approval is received.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import socket
import threading
import time

BASE = Path(__file__).resolve().parents[2]
BUILD = BASE / 'build'
TEMPLATE = Path(__file__).with_name('Caddyfile.synthetic.template')
MAX_STREAM = 128 * 1024 * 1024
CHUNK = 64 * 1024


def checked_port(value):
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError('Ports must be integers in 1024..65535')
    return value


def render_config(proxy_port, fixture_port, storage):
    """Pure rendering; does not create storage, execute Caddy or bind a port."""
    checked_port(proxy_port)
    checked_port(fixture_port)
    if proxy_port == fixture_port:
        raise ValueError('Proxy and fixture ports must differ')
    path = Path(storage)
    if not path.is_absolute() or not BUILD.is_dir():
        raise ValueError('Use an absolute fresh storage path under build')
    build = BUILD.resolve(strict=True)
    resolved = path.resolve(strict=False)
    if resolved.parent != build or resolved.exists() or not re.fullmatch(r'caddy-synthetic-[a-z0-9-]{4,48}', resolved.name):
        raise ValueError('Use a fresh direct child of build named caddy-synthetic-...')
    # JSON double-quoted syntax safely escapes Windows separators and quotes.
    return (TEMPLATE.read_text(encoding='utf-8')
            .replace('__PROXY_PORT__', str(proxy_port))
            .replace('__UPSTREAM_PORT__', str(fixture_port))
            .replace('__STORAGE_PATH__', json.dumps(resolved.as_posix(), ensure_ascii=True)))


def write_config_once(path, text):
    """Refuse overwrite; restrict generated config to task build directory."""
    destination = Path(path)
    if not destination.is_absolute() or destination.resolve(strict=False).parent != BUILD.resolve(strict=True):
        raise ValueError('Config must be a new direct child of task build')
    with destination.open('x', encoding='utf-8', newline='\n') as output:
        output.write(text)


class Fixture(ThreadingHTTPServer):
    # Handler threads are tracked by ThreadingHTTPServer. Teardown wakes only
    # owned sockets and reports uncertainty if any join cannot finish.
    daemon_threads = False
    allow_reuse_address = False
    def __init__(self, port, marker):
        self.marker = marker
        self.stream_done = threading.Event()
        self.stream_state = 'not-started'
        self._request_lock = threading.Lock()
        self._requests = set()
        super().__init__(('127.0.0.1', checked_port(port)), Handler)

    def get_request(self):
        request, address = super().get_request()
        with self._request_lock:
            self._requests.add(request)
        return request, address

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self._request_lock:
                self._requests.discard(request)

    def close_owned_requests(self):
        # Normally called after serve_forever stops. If that stop times out,
        # the harness also closes its own listener and reports uncertainty.
        with self._request_lock:
            requests = tuple(self._requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                request.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):
        # Do not log requests, paths or values.
        return


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path == '/fixture/ready':
            data = self.server.marker.encode('ascii')
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)
        elif self.path == '/fixture/stream':
            self.send_response(200)
            self.send_header('Content-Length', str(MAX_STREAM))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.server.stream_state = 'started'
            try:
                deadline = time.monotonic() + 8
                for _ in range(MAX_STREAM // CHUNK):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.server.stream_state = 'io-uncertain'
                        return
                    self.connection.settimeout(remaining)
                    self.wfile.write(b'x' * CHUNK)
                    self.wfile.flush()
                self.server.stream_state = 'fully-buffered'
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.server.stream_state = 'peer-closed'
            except (OSError, TimeoutError):
                self.server.stream_state = 'io-uncertain'
            finally:
                self.server.stream_done.set()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != '/fixture/body':
            self.send_error(404)
            return
        try:
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= size <= 1024:
                self.send_error(400)
                return
            deadline = time.monotonic() + 8
            remaining_bytes = size
            while remaining_bytes:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    return
                self.connection.settimeout(remaining_time)
                chunk = self.rfile.read1(min(remaining_bytes, 1024))
                if not chunk:
                    return
                remaining_bytes -= len(chunk)
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
        except (ValueError, OSError, TimeoutError):
            return


def closed_or_timeout_response(port, payload):
    """An expected closure/timeout response, not proof of its underlying cause."""
    with socket.create_connection(('127.0.0.1', checked_port(port)), timeout=2) as stream:
        stream.settimeout(6)
        started = time.monotonic()
        stream.sendall(payload)
        try:
            data = stream.recv(4096)
        except socket.timeout:
            return 'inconclusive'
        except (ConnectionResetError, ConnectionAbortedError):
            data = b''
        elapsed = time.monotonic() - started
        status = data.split(b'\r\n', 1)[0]
        if elapsed < 1 or elapsed > 6:
            return 'inconclusive'
        if data == b'' or any(code in status for code in (b' 400 ', b' 408 ', b' 502 ', b' 503 ', b' 504 ')):
            return 'closure-observed'
        return 'inconclusive'


def ready(port, marker):
    request = b'GET /fixture/ready HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n'
    try:
        with socket.create_connection(('127.0.0.1', checked_port(port)), timeout=1) as stream:
            stream.settimeout(1)
            stream.sendall(request)
            response = bytearray()
            while len(response) < 4096:
                data = stream.recv(4096 - len(response))
                if not data:
                    break
                response.extend(data)
        return b' 200 ' in bytes(response).split(b'\r\n', 1)[0] and marker.encode('ascii') in response
    except (OSError, TimeoutError):
        return False


def bounded_fixture_shutdown(server, serve_thread, seconds=5):
    """Wake owned sockets and bound joins; raise if cleanup is uncertain."""
    errors = []
    def call(method):
        try:
            method()
        except BaseException:
            errors.append(True)
    stopper = threading.Thread(target=call, args=(server.shutdown,), daemon=True)
    stopper.start()
    stopper.join(seconds)
    if stopper.is_alive():
        # This is the listener created by Fixture, not any other process's
        # listener. Even if closing wakes the loop, the outcome is uncertain.
        errors.append(True)
        try:
            server.socket.close()
        except OSError:
            pass
    server.close_owned_requests()
    closer = threading.Thread(target=call, args=(server.server_close,), daemon=True)
    closer.start()
    closer.join(seconds)
    serve_thread.join(seconds)
    if errors or stopper.is_alive() or closer.is_alive() or serve_thread.is_alive():
        raise RuntimeError('Synthetic fixture shutdown uncertain; stop the owned harness process')


def run_probe(proxy_port, fixture_port):
    """Explicit opt-in. Opens only loopback sockets; never starts or stops Caddy."""
    checked_port(proxy_port)
    checked_port(fixture_port)
    if proxy_port == fixture_port:
        raise ValueError('Proxy and fixture ports must differ')
    marker = 'synthetic-' + secrets.token_hex(16)
    server = Fixture(fixture_port, marker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print('Synthetic fixture is running. Start the separately approved Caddy process now.', flush=True)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not ready(proxy_port, marker):
            time.sleep(0.2)
        if not ready(proxy_port, marker):
            return {'setup': 'inconclusive'}
        results = {}
        results['incomplete_header'] = closed_or_timeout_response(
            proxy_port, b'GET /fixture/ready HTTP/1.1\r\nHost:')
        results['incomplete_body'] = closed_or_timeout_response(
            proxy_port, b'POST /fixture/body HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 100\r\n\r\nx')
        # Stop reading from this client. A peer close in the upstream fixture
        # after the deadline is only consistent with the timeout; Caddy logs
        # and healthy-stream controls are needed to identify the actual cause.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            stream.settimeout(2)
            stream.connect(('127.0.0.1', proxy_port))
            stream.sendall(b'GET /fixture/stream HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n')
            completed = server.stream_done.wait(5)
            results['nonreading_client'] = ('peer-closed-observed'
                if completed and server.stream_state == 'peer-closed' else 'inconclusive')
        results['control_after'] = 'ok' if ready(proxy_port, marker) else 'inconclusive'
        return results
    finally:
        bounded_fixture_shutdown(server, thread)


def main():
    parser = argparse.ArgumentParser(description='Local synthetic Caddy experiment; never launches Caddy')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('render', 'run'):
        p = sub.add_parser(action)
        p.add_argument('--proxy-port', type=int, required=True)
        p.add_argument('--fixture-port', type=int, required=True)
        if action == 'render':
            p.add_argument('--storage', required=True)
            p.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.action == 'render':
        write_config_once(args.output, render_config(args.proxy_port, args.fixture_port, args.storage))
        print('Synthetic config prepared; Caddy has not been run.')
        return 0
    observations = run_probe(args.proxy_port, args.fixture_port)
    print(json.dumps(observations, sort_keys=True))
    # Nonzero means setup or observations were inconclusive; even zero means
    # observations only, not proof of causation or production readiness.
    return 0 if observations == {'incomplete_header':'closure-observed',
        'incomplete_body':'closure-observed', 'nonreading_client':'peer-closed-observed',
        'control_after':'ok'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
