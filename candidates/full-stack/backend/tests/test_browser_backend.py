"""The real-browser checks' backend answers as quickly as the documented server.

tests/browser_backend.py serves Uvicorn on a listener it binds itself. Unless that listener is an IPPROTO_TCP
socket, asyncio leaves TCP_NODELAY off, and every response after a connection's first then reaches the client
about 40 ms after its headers (Nagle's algorithm waiting for the client's delayed ACK). The documented server
(``python -m mom_health_backend``) answers within a few milliseconds, so the browser checks would see a slower
server than the one they stand for. One check times the responses; the other runs main() with every way into Uvicorn's
server class made to serve nothing, and pins the one Uvicorn server main() starts and the socket it hands that server,
which no load on the machine can blur. A listening socket or a server other than Uvicorn's that main() also opens is
not counted; one that keeps main() from returning fails the check after OVERDUE seconds instead of hanging it. Only
browser_backend's threading.Thread is replaced, though: a thread or process that main() starts another way
(concurrent.futures, asyncio.to_thread, multiprocessing) keeps running after that failure, and keeps the run from
exiting where it is not a daemon thread. Loopback only, fictional token, a fresh fixture under build/.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TOKEN = 'synthetic-browser-token-not-for-deployment-0001'  # the test constant browser_backend.py serves
OVERDUE = 20  # seconds; main() returns within about 0.3 s when nothing it starts runs on


class Overdue(SystemExit):
    """main() has not returned in time. A SystemExit, so that neither an except Exception in main() nor asyncio, which
    passes on only SystemExit and KeyboardInterrupt from its tasks and callbacks, swallows it (not a
    KeyboardInterrupt, which uvicorn.run catches)."""


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
        # the check below pins the socket main() serves on.
        self.assertLess(sorted(delays[1:])[3], 0.02, 'response bodies trailed their headers by (s): %s' % ['%.4f' % d for d in delays])

    def test_main_serves_on_an_ipproto_tcp_socket_on_loopback(self):
        spec = importlib.util.spec_from_file_location('browser_backend', HERE / 'browser_backend.py')
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        # Uvicorn's server class itself is patched, so main() is caught whatever name it reaches it by (uvicorn.Server,
        # uvicorn.server.Server, uvicorn.run, a subclass) and whichever of its entry points it serves through (run;
        # serve, the coroutine that run() wraps; or startup, through which _serve() and every other way to a listening
        # socket goes, with main_loop and shutdown made to return at once): every Uvicorn server is counted, and none
        # serves. Each socket handed over is closed after the check, even if main() fails after it.
        server_class, servers, served = backend.uvicorn.server.Server, [], []
        initialize = server_class.__init__

        def counted(server, *arguments, **options):
            servers.append(server)
            initialize(server, *arguments, **options)

        def recorded(server, sockets=None):
            sockets = list(sockets or [])  # an iterator would be spent by the first loop
            for listener in sockets:
                self.addCleanup(listener.close)
            served.extend(sockets)
            server.started = True

        async def recorded_async(server, sockets=None):
            recorded(server, sockets)

        async def idle(server, *arguments, **options):
            pass

        class Thread:
            """Stands in for threading.Thread in browser_backend alone: runs its target at start(), so nothing main()
            starts through it (its stdin reader included) outlives the check. A target that never returns (an event
            loop run forever, a server other than Uvicorn's) would hang the check: the alarm below fails it instead."""
            def __init__(self, target, args=(), kwargs=None, daemon=None):
                self.target, self.args, self.kwargs = target, args, kwargs or {}

            def start(self):
                self.target(*self.args, **self.kwargs)

            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass
        handlers = types.SimpleNamespace(signal=lambda *arguments: None, SIGTERM=signal.SIGTERM, SIGINT=signal.SIGINT)

        def overdue(signum, frame):
            raise Overdue('main() had not returned after %d s: something it started still runs inside this check' % OVERDUE)
        alarm = hasattr(signal, 'setitimer') and threading.current_thread() is threading.main_thread()
        if alarm:
            # A timer already running (a watchdog around the whole run, say) is given back what was left of it, at least
            # a moment, so that one due meanwhile still fires. Cleanups run last in, first out: this timer off, the
            # handler back, then that timer.
            outer, armed = signal.getitimer(signal.ITIMER_REAL), time.monotonic()

            def give_back():
                if outer[0]:
                    signal.setitimer(signal.ITIMER_REAL, max(outer[0] - (time.monotonic() - armed), 0.001), outer[1])
            self.addCleanup(give_back)
            self.addCleanup(signal.signal, signal.SIGALRM, signal.signal(signal.SIGALRM, overdue))
            self.addCleanup(signal.setitimer, signal.ITIMER_REAL, 0)
            signal.setitimer(signal.ITIMER_REAL, OVERDUE)
        try:
            with patch.object(server_class, '__init__', counted), patch.object(server_class, 'run', recorded), \
                    patch.object(server_class, 'serve', recorded_async), patch.object(server_class, 'startup', recorded_async), \
                    patch.object(server_class, 'main_loop', idle), patch.object(server_class, 'shutdown', idle), \
                    patch.object(backend, 'threading', types.SimpleNamespace(Thread=Thread)), patch.object(backend, 'signal', handlers), \
                    patch.object(backend.sys, 'argv', ['browser_backend.py']), patch.object(backend.sys, 'stdin', io.StringIO('')), \
                    contextlib.redirect_stdout(io.StringIO()):
                backend.main()
        except Overdue as late:
            self.fail(str(late))
        finally:
            if alarm:
                signal.setitimer(signal.ITIMER_REAL, 0)
        self.assertEqual(1, len(servers), 'main() started %d servers' % len(servers))
        self.assertEqual(1, len(served), served)
        self.assertEqual(socket.IPPROTO_TCP, served[0].proto, 'asyncio sets TCP_NODELAY only on an IPPROTO_TCP socket')
        self.assertEqual('127.0.0.1', served[0].getsockname()[0])


class OverdueAlarmTests(unittest.TestCase):
    """The structural check's alarm, without waiting OVERDUE seconds for it."""

    def test_the_alarm_reaches_main_through_an_event_loop_of_its_own(self):
        # Where main() runs an event loop, the alarm lands in one of its tasks or callbacks, which keep any other
        # exception to themselves (logged, or kept in the task), so that the loop and main() would run on.
        import asyncio

        def overdue():
            raise Overdue('synthetic: the alarm')
        tasks = []

        async def in_a_task():
            async def busy():
                overdue()
            tasks.append(asyncio.get_running_loop().create_task(busy()))
            await asyncio.sleep(2)

        async def in_a_callback():
            asyncio.get_running_loop().call_soon(overdue)
            await asyncio.sleep(2)
        for where in (in_a_task, in_a_callback):
            with self.subTest(where.__name__):
                started = time.monotonic()
                with self.assertRaises(Overdue):
                    asyncio.run(where())
                self.assertLess(time.monotonic() - started, 1, 'the loop ran on after the alarm')
        for task in tasks:  # the task keeps the alarm too: taken from it, so that asyncio does not log it as never retrieved
            self.assertIsInstance(task.exception(), Overdue)

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'needs setitimer')
    def test_the_alarm_gives_back_a_timer_already_running(self):
        if threading.current_thread() is not threading.main_thread():
            self.skipTest('signals reach only the main thread')
        fired = []

        def watchdog(signum, frame):
            fired.append(signum)
        self.addCleanup(signal.signal, signal.SIGALRM, signal.signal(signal.SIGALRM, watchdog))
        self.addCleanup(signal.setitimer, signal.ITIMER_REAL, 0)
        signal.setitimer(signal.ITIMER_REAL, 600, 300)  # a watchdog around the whole run, repeating
        result = unittest.TestResult()
        BrowserBackendTimingTests('test_main_serves_on_an_ipproto_tcp_socket_on_loopback').run(result)
        self.assertEqual([], result.errors + result.failures, 'the structural check itself failed')
        left, interval = signal.getitimer(signal.ITIMER_REAL)
        self.assertGreater(left, 500, 'the watchdog was not given back what was left of it')
        self.assertEqual(300, interval, "the watchdog's interval was not given back")
        self.assertIs(watchdog, signal.getsignal(signal.SIGALRM), "the watchdog's handler was not given back")
        self.assertEqual([], fired)

if __name__ == '__main__':
    unittest.main()
