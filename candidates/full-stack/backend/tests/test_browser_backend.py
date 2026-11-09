"""The real-browser checks' backend answers as quickly as the documented server.

tests/browser_backend.py serves Uvicorn on a listener it binds itself. Unless that listener is an IPPROTO_TCP
socket, asyncio leaves TCP_NODELAY off, and every response after a connection's first then reaches the client
about 40 ms after its headers (Nagle's algorithm waiting for the client's delayed ACK). The documented server
(``python -m mom_health_backend``) answers within a few milliseconds, so the browser checks would see a slower
server than the one they stand for. One check times the responses; the other runs main() with every way into Uvicorn's
server class made to serve nothing, and pins the one Uvicorn server main() starts and the socket it hands that server,
which no load on the machine can blur. A listening socket or a server other than Uvicorn's that main() also opens is
not counted; one that keeps main() from returning fails the check after OVERDUE seconds instead of hanging it (the
alarm comes again every second until main() has returned, so a main() that waits for what it started as it unwinds,
as a with ThreadPoolExecutor() block or asyncio.run() around asyncio.to_thread does, fails a second or two later).
Only browser_backend's threading.Thread is replaced, though: a thread or process that main() starts another way
(concurrent.futures, asyncio.to_thread, multiprocessing) keeps running after that failure, and keeps the run from
exiting where it is not a daemon thread. Loopback only, fictional token, a fresh fixture under build/.
"""
import concurrent.futures
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


def alarms_here():
    """Whether a SIGALRM timer can be used here: setitimer exists, and this is the main thread (the only one that
    signals reach)."""
    return hasattr(signal, 'setitimer') and threading.current_thread() is threading.main_thread()


@contextlib.contextmanager
def own_alarm(handler):
    """SIGALRM goes to handler for the block, which has the timer to itself: a timer that was running (a watchdog
    around the whole run, say) is turned off as the block starts, so that it cannot fire into handler. Once the block
    has ended, an alarm still due goes nowhere, so that nothing handler would raise cuts short what follows: the timer
    is turned off again, the handler before it put back, and only then (so that it cannot reach handler) that earlier
    timer given back what was left of it, with its interval, or at least a moment should it have come due meanwhile."""
    live = True

    def during(signum, frame):
        if live:
            handler(signum, frame)
    outer, armed = signal.setitimer(signal.ITIMER_REAL, 0), time.monotonic()
    before = signal.signal(signal.SIGALRM, during)
    try:
        yield
    finally:
        live = False
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, before)
        # Read just as it came due, before its alarm was taken, a repeating timer shows nothing left, only its interval.
        if outer[0] or outer[1]:
            signal.setitimer(signal.ITIMER_REAL, max(outer[0] - (time.monotonic() - armed), 0.001), outer[1])


@contextlib.contextmanager
def overdue_alarm(seconds=OVERDUE, again=1):
    """Raises Overdue in the main thread once the block has run for seconds, and again every again seconds until it
    ends: a block that catches the first one, or waits for what it started while it unwinds, still ends. Nothing
    where alarms cannot be used (see alarms_here())."""
    if not alarms_here():
        yield
        return

    def overdue(signum, frame):
        raise Overdue('main() had not returned after %g s: something it started still runs inside this check' % seconds)
    with own_alarm(overdue):
        signal.setitimer(signal.ITIMER_REAL, seconds, again)
        yield


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
        # The alarm is set last, so that it is off again before anything else is undone.
        try:
            with patch.object(server_class, '__init__', counted), patch.object(server_class, 'run', recorded), \
                    patch.object(server_class, 'serve', recorded_async), patch.object(server_class, 'startup', recorded_async), \
                    patch.object(server_class, 'main_loop', idle), patch.object(server_class, 'shutdown', idle), \
                    patch.object(backend, 'threading', types.SimpleNamespace(Thread=Thread)), patch.object(backend, 'signal', handlers), \
                    patch.object(backend.sys, 'argv', ['browser_backend.py']), patch.object(backend.sys, 'stdin', io.StringIO('')), \
                    contextlib.redirect_stdout(io.StringIO()), overdue_alarm():
                backend.main()
        except Overdue as late:
            self.fail(str(late))
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

    def watchdog(self):
        """A stand-in for a watchdog around the whole run, which records when it fires: its handler for the test, and a
        real one already running given back afterwards (own_alarm())."""
        if not hasattr(signal, 'setitimer') or threading.current_thread() is not threading.main_thread():
            self.skipTest('needs setitimer, in the main thread (signals reach only that)')
        fired, stack = [], contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(own_alarm(lambda signum, frame: fired.append(time.monotonic())))
        return fired

    def test_the_alarm_gives_back_a_timer_already_running(self):
        fired = self.watchdog()
        handler = signal.getsignal(signal.SIGALRM)
        armed = time.monotonic()
        signal.setitimer(signal.ITIMER_REAL, 600, 300)  # repeating
        # How the structural check sets its alarm: each setitimer() as (seconds, interval, whether stdout is this
        # check's, that is, not yet or no longer redirected by the structural check).
        calls, real_setitimer, outside = [], signal.setitimer, sys.stdout

        def recorded(which, seconds, interval=0):
            calls.append((round(seconds, 2), interval, sys.stdout is outside))
            return real_setitimer(which, seconds, interval)
        result = unittest.TestResult()
        with patch.object(signal, 'setitimer', recorded):
            BrowserBackendTimingTests('test_main_serves_on_an_ipproto_tcp_socket_on_loopback').run(result)
        self.assertEqual([], result.errors + result.failures, 'the structural check itself failed')
        self.assertIn((OVERDUE, 1, False), calls, 'the structural check did not set its alarm, repeating every second, inside its patches')
        self.assertEqual((0, 0, False), calls[calls.index((OVERDUE, 1, False)) + 1], 'its alarm was not turned off before its patches were undone')
        left, interval = signal.getitimer(signal.ITIMER_REAL)
        self.assertLess(abs(left - (600 - (time.monotonic() - armed))), 0.05, 'the watchdog was not given back what was left of it')
        self.assertEqual(300, interval, "the watchdog's interval was not given back")
        self.assertIs(handler, signal.getsignal(signal.SIGALRM), "the watchdog's handler was not given back")
        self.assertEqual([], fired)

    def test_these_checks_give_back_a_watchdog_around_them(self):
        fired = self.watchdog()
        armed = time.monotonic()
        signal.setitimer(signal.ITIMER_REAL, 600)
        result = unittest.TestResult()
        names = ('test_the_alarm_gives_back_a_timer_already_running', 'test_the_alarm_has_the_timer_to_itself',
                 'test_the_alarm_gives_back_a_repeating_timer_read_as_it_came_due', 'test_the_alarm_gives_back_what_was_left_or_at_least_a_moment',
                 'test_the_alarm_gives_back_the_handler_before_the_timer', 'test_an_alarm_due_as_the_block_ends_cuts_nothing_short',
                 'test_the_alarm_comes_again_while_the_block_unwinds')
        for name in names:
            OverdueAlarmTests(name).run(result)
        self.assertEqual([], result.errors + result.failures, 'a check failed')
        self.assertEqual(len(names), result.testsRun)
        left = signal.getitimer(signal.ITIMER_REAL)[0]
        self.assertLess(abs(left - (600 - (time.monotonic() - armed))), 0.05, 'the watchdog was not given back what was left of it')
        self.assertEqual([], fired)

    def test_the_alarm_has_the_timer_to_itself(self):
        # A watchdog around the block, due while it runs, fires after it (into its own handler), not into the block's.
        fired, inside = self.watchdog(), []
        signal.setitimer(signal.ITIMER_REAL, 0.2)
        with own_alarm(lambda signum, frame: inside.append(time.monotonic())):
            self.assertEqual((0.0, 0.0), signal.getitimer(signal.ITIMER_REAL), 'the timer around the block still ran inside it')
            time.sleep(0.6)
        ended = time.monotonic()
        while not fired and time.monotonic() - ended < 1:
            time.sleep(0.01)
        self.assertEqual([], inside, "the timer around the block fired into the block's handler")
        self.assertTrue(fired, 'the timer around the block never fired')

    def test_the_alarm_gives_back_a_repeating_timer_read_as_it_came_due(self):
        # Read just as it comes due, before its alarm is taken, a repeating timer shows nothing left, only its
        # interval: what Linux gives for one in that state, which lasts microseconds, so setitimer() is faked here.
        self.watchdog()
        calls = []

        def due(which, seconds, interval=0):
            calls.append((seconds, interval))
            return (0.0, 0.5) if len(calls) == 1 else (0.0, 0.0)
        with patch.object(signal, 'setitimer', due):
            with own_alarm(lambda signum, frame: None):
                pass
        self.assertEqual([(0, 0), (0, 0), (0.001, 0.5)], calls, 'turned off, off again, then given back at once with its interval')

    def test_an_alarm_due_as_the_block_ends_cuts_nothing_short(self):
        # The block's alarm comes due just as its timer is turned off (raised here as that call returns): it goes
        # nowhere, and the handler and a watchdog around the block still come back.
        fired = self.watchdog()
        handler = signal.getsignal(signal.SIGALRM)
        armed = time.monotonic()
        signal.setitimer(signal.ITIMER_REAL, 600, 300)
        real_setitimer, ending = signal.setitimer, []

        def late(which, seconds, interval=0):
            was = real_setitimer(which, seconds, interval)
            if seconds == 0 and ending:
                del ending[:]
                signal.raise_signal(signal.SIGALRM)
            return was
        with patch.object(signal, 'setitimer', late):
            with overdue_alarm(60):
                ending.append(True)
        self.assertEqual([], ending, 'the alarm was never made due')
        self.assertIs(handler, signal.getsignal(signal.SIGALRM), 'the handler before the block was not put back')
        left, interval = signal.getitimer(signal.ITIMER_REAL)
        self.assertLess(abs(left - (600 - (time.monotonic() - armed))), 0.05, 'the watchdog was not given back what was left of it')
        self.assertEqual(300, interval)
        self.assertEqual([], fired)

    def test_the_alarm_gives_back_what_was_left_or_at_least_a_moment(self):
        fired = self.watchdog()
        armed = time.monotonic()
        signal.setitimer(signal.ITIMER_REAL, 5)
        with overdue_alarm(60):
            time.sleep(0.3)
        left = signal.getitimer(signal.ITIMER_REAL)[0]
        self.assertLess(abs(left - (5 - (time.monotonic() - armed))), 0.05, 'not given back what was left of it, %.3f s' % left)
        # One due while the block ran fires as soon as it has ended.
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        with overdue_alarm(60):
            time.sleep(0.3)
        ended = time.monotonic()
        while not fired and time.monotonic() - ended < 1:
            time.sleep(0.01)
        self.assertTrue(fired, 'a watchdog due while the block ran never fired')
        self.assertLess(fired[0] - ended, 0.5)

    def test_the_alarm_gives_back_the_handler_before_the_timer(self):
        self.watchdog()
        signal.setitimer(signal.ITIMER_REAL, 5)
        calls, real_signal, real_setitimer = [], signal.signal, signal.setitimer

        def recorded_signal(signum, handler):
            calls.append(('handler', handler))
            return real_signal(signum, handler)

        def recorded_setitimer(which, seconds, interval=0):
            calls.append(('timer', round(seconds)))
            return real_setitimer(which, seconds, interval)
        before = signal.getsignal(signal.SIGALRM)
        with patch.object(signal, 'signal', recorded_signal), patch.object(signal, 'setitimer', recorded_setitimer):
            with overdue_alarm(60):
                del calls[:]  # what was done to set it up
        self.assertEqual([('timer', 0), ('handler', before), ('timer', 5)], calls)

    def test_the_alarm_comes_again_while_the_block_unwinds(self):
        # main() catches the first alarm, or waits for what it started while it unwinds: the next one ends it.
        self.watchdog()
        release = threading.Event()
        self.addCleanup(release.set)  # the waits it started end after the check

        def caught():
            try:
                release.wait(5)
            except Overdue:
                release.wait(5)

        def with_an_executor():
            with concurrent.futures.ThreadPoolExecutor(1) as pool:  # whose exit waits for its worker
                pool.submit(release.wait, 5).result()

        def with_asyncio_to_thread():
            async def serving():
                await asyncio.to_thread(release.wait, 5)
            asyncio.run(serving())  # which waits for the executor's worker as it ends
        import asyncio
        for main in (caught, with_an_executor, with_asyncio_to_thread):
            with self.subTest(main.__name__):
                if main is with_asyncio_to_thread and not hasattr(asyncio, 'to_thread'):
                    self.skipTest('needs asyncio.to_thread (Python 3.9 or later)')
                started = time.monotonic()
                with self.assertRaises(Overdue):
                    with overdue_alarm(0.2, 0.2):
                        main()
                self.assertLess(time.monotonic() - started, 2, 'the block ran on after the second alarm')

if __name__ == '__main__':
    unittest.main()
