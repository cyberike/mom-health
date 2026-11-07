"""Opt-in checks of the throwaway PostgreSQL launcher itself. Fictional data only.

Not collected by ``unittest discover``. The launch checks need Linux and the
PostgreSQL server binaries; as root, the launcher's server runs as the postgres
account, and the failed-setup check also runs once as that account without
--run-as. Each launch check runs a private copy of the launcher in a unique
directory (under build/, deep enough that its socket goes in a private
directory under /tmp; one layout of the ownership check uses a short copy under
/tmp, whose socket stays in the cluster), with a small probe command and
--discard, so every cluster is removed again and concurrent runs cannot disturb
each other. The other checks need only POSIX, except three that read /proc and
are skipped without it: the environment and working-directory checks of the
cleanup (Linux) and the runuser step check (Linux, root and runuser). The
checks that need no server also run under every other CPython 3 installed in
/usr/bin or /usr/local/bin (before 3.13, communicate() flushes a stdin that
run() has closed). The module imports anywhere. From candidates/full-stack/backend:

    python -m unittest tests.postgresql_launcher_checks -v
"""
import itertools
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import postgresql_throwaway as launcher  # noqa: E402

ROOT = os.name == 'posix' and os.geteuid() == 0
RUN_AS = ['--run-as', 'postgres'] if ROOT else []
SIGNALS = tuple(getattr(signal, name) for name in ('SIGINT', 'SIGTERM', 'SIGHUP') if hasattr(signal, name))
STARTED = 'Throwaway PostgreSQL ('
SOCKETS = 'Throwaway PostgreSQL socket directory'
WAITING = r'''
import os, sys, time
print(os.getpid(), flush=True)
time.sleep(120)
'''
PROBE = r'''
import json, os
from pathlib import Path
cluster = Path(os.environ['MOM_HEALTH_TEST_POSTGRESQL_CLUSTER'])
info = json.loads((cluster / 'cluster.json').read_text())
owner = lambda path: os.stat(path).st_uid
print(json.dumps({'libpq': sorted(name for name in os.environ if name.startswith('PG')),
                  'dsn': os.environ['MOM_HEALTH_TEST_POSTGRESQL_DSN'], 'sockets': info['sockets'],
                  'socket_dir': [os.stat(info['sockets']).st_mode & 0o7777, owner(info['sockets'])],
                  'owners': {'cluster': owner(cluster), 'metadata': owner(cluster / 'cluster.json'), 'data': owner(info['data']),
                             'config': owner(Path(info['data']) / 'postgresql.conf'), 'log': owner(Path(info['log']).parent)}}))
'''
HANGUP_PROBE = r'''
import signal, time
print('SIGHUP ignored:', signal.getsignal(signal.SIGHUP) == signal.SIG_IGN, flush=True)
time.sleep(3)
print('finished normally', flush=True)
'''
# Waits until the file exists, or 120 s, so an interrupted check (whose cleanups do not run) cannot keep it, and
# with it the launcher and its server, running for good.
UNTIL = r'''
import os, time
end = time.monotonic() + 120
while not os.path.exists(%r) and time.monotonic() < end:
    time.sleep(0.05)
'''
# Runs the launcher with a SIGTERM to itself just before or just after Popen has started the command,
# which is where a signal sent to the launcher alone lands if it arrives while the command starts.
SIGNAL_WHILE_STARTING = r'''
import os, runpy, signal, subprocess, sys
when, marker, launcher = sys.argv[1:4]


class Popen(subprocess.Popen):
    def __init__(self, args, *rest, **options):
        command = marker in str(args)
        if command and when == 'before':
            os.kill(os.getpid(), signal.SIGTERM)
        super().__init__(args, *rest, **options)
        if command:
            print('command started as', self.pid, file=sys.stderr, flush=True)
            if when == 'after':
                os.kill(os.getpid(), signal.SIGTERM)


subprocess.Popen = Popen
sys.argv = sys.argv[3:]
runpy.run_path(launcher, run_name='__main__')
'''
# The launcher failing before its first status line: find_bindir() fails in one of two ways, or (usage) the
# arguments are wrong. "failed" goes to stdout once main() has failed, just before the exit. SIGINT is Python's
# KeyboardInterrupt, as at a terminal, whatever this test process inherited.
EARLY_ERROR = r'''
import signal, sys
signal.signal(signal.SIGINT, signal.default_int_handler)
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
failure = sys.argv[2]


def find_bindir(explicit=None):
    if failure == 'message':
        raise SystemExit('PostgreSQL server binaries (initdb, pg_ctl) not found; pass --bindir')
    raise PermissionError(13, 'Permission denied (synthetic)')


if failure != 'usage':
    launcher.find_bindir = find_bindir
sys.argv = ['postgresql_throwaway.py'] + sys.argv[3:]
try:
    sys.exit(launcher.main())
finally:
    print('failed', flush=True)
'''
# Status lines on a non-blocking stderr: the first meets a full pipe, the rest come after the parent drained it.
FULL_STREAM = r'''
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
launcher.say('status line 1')
print('said', flush=True)
sys.stdin.readline()  # the parent has drained the pipe
launcher.say('status line 2')
launcher.say('status line 3')
subprocess.run([sys.executable, '-c', 'import sys; sys.stderr.write("command stderr line\\n")'])
'''
# A last status line that meets a full non-blocking stderr, then the exit, with flush_stderr() registered as main() does.
FULL_AT_EXIT = r'''
import atexit, sys
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
atexit.register(launcher.flush_stderr)
launcher.say('last status line')
print('exiting', flush=True)
'''
# A command that leaves a process behind which uses the cluster without naming it: it has the data directory as
# PGDATA, or as its working directory. The process ends once RELEASE exists, or after 30 s: less than STOP_GRACE,
# so an interrupted check (whose cleanups do not run) leaves no cluster behind.
LEAVES_A_PROCESS = r'''
import os, subprocess, sys
release, how = %r, %r
data = os.path.join(os.environ['MOM_HEALTH_TEST_POSTGRESQL_CLUSTER'], 'data')
wait = 'import os, time\nend = time.monotonic() + 30\nwhile not os.path.exists(%%r) and time.monotonic() < end:\n    time.sleep(0.05)'
waiter = subprocess.Popen([sys.executable, '-c', wait %% release], env=dict(os.environ, PGDATA=data) if how == 'PGDATA' else None,
                          cwd=data if how == 'working directory' else None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True)
print(waiter.pid, flush=True)
'''
WAYS_TO_USE_THE_CLUSTER = ('PGDATA', 'working directory')
# The launcher with a STOP_GRACE of 3 s, so a check can see what the cleanup does once that wait is over.
SHORT_STOP_GRACE = r'''
import sys
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
launcher.STOP_GRACE = 3
sys.argv = sys.argv[2:]
sys.exit(launcher.main())
'''
# A command that asks the launcher's own leftover_processes() which processes of the running server it sees.
# The postmaster's children (checkpointer, background writer, ...) overwrite their command line and environment.
SEES_THE_SERVER = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, %r)
import postgresql_throwaway as launcher
info = launcher.describe(os.environ['MOM_HEALTH_TEST_POSTGRESQL_CLUSTER'])
postmaster = int((Path(info['data']) / 'postmaster.pid').read_text().split()[0])


def children():
    found = set()
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            if int((entry / 'stat').read_text().rsplit(')', 1)[1].split()[1]) == postmaster:
                found.add(int(entry.name))
        except (OSError, IndexError, ValueError):
            pass
    return found


before = children()
found = launcher.leftover_processes(info)
lasting = before & children()  # there throughout, unlike an autovacuum worker that came and went meanwhile
print(json.dumps({'postmaster': postmaster, 'children': sorted(lasting), 'found': sorted(found)}))
'''
# A status line written after the reader went away, then an exit with the given status.
CLOSED_STREAM = r'''
import sys
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
sys.stdin.readline()  # the parent has closed its end of our stderr
for line in range(3):
    launcher.say('Throwaway PostgreSQL status line %d' % line)
sys.exit(143)
'''


def binaries_available():
    try:
        return (launcher.find_bindir() / 'initdb').exists()
    except SystemExit:
        return False


def server_uid():
    import pwd  # POSIX only; imported here so the module imports anywhere
    return pwd.getpwnam('postgres').pw_uid if ROOT else os.getuid()


def _cmdline(entry):
    try:
        return (entry / 'cmdline').read_bytes()
    except OSError:
        return b''


def processes_naming(path, marker=b''):
    return [entry.name for entry in Path('/proc').glob('[0-9]*')
            if str(path).encode() in _cmdline(entry) and marker in _cmdline(entry)]


def initdb_runs_a_backend(root):
    """True once initdb from this copy has started its bootstrap or single-user backend. By then
    initdb handles signals itself and ignores SIGPIPE, so an orphaned initdb would finish its work."""
    initdb = {entry.name for entry in Path('/proc').glob('[0-9]*')
              if _cmdline(entry).split(b'\0')[0].endswith(b'/initdb') and str(root).encode() in _cmdline(entry)}
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            child = (entry / 'stat').read_text().rsplit(')', 1)[1].split()[1] in initdb
        except (OSError, IndexError):
            continue
        # Not its earlier "postgres -V" probe, which runs before initdb ignores SIGPIPE.
        if child and any(flag in _cmdline(entry) for flag in (b'--boot', b'--single')):
            return True
    return False


def remove_copy(root):
    """Stop anything still running from a private copy (only after a failed check), then remove it."""
    for signum in (signal.SIGQUIT, signal.SIGKILL):
        for pid in processes_naming(root):
            try:
                os.kill(int(pid), signum)
            except OSError:
                pass
        deadline = time.monotonic() + 10
        while processes_naming(root) and time.monotonic() < deadline:
            time.sleep(0.1)
    shutil.rmtree(root, ignore_errors=True)


def private_copy(test, owner=None, short=False):
    """A launcher copy in a unique directory, removed after the test. Under build/ its in-cluster socket
    path is longer than a Unix socket allows; a short copy under /tmp keeps its socket in the cluster."""
    launcher.FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='lc-' if short else 'launcher-check-', dir='/tmp' if short else launcher.FIXTURES.parent))
    test.addCleanup(remove_copy, root)
    root.chmod(0o755)  # the server account must reach data/ inside it
    script = root / ('' if short else 'deep-checkout-' + 'd' * 16) / 'backend' / 'tests' / 'postgresql_throwaway.py'
    script.parent.mkdir(parents=True)
    shutil.copy(launcher.__file__, script)
    fixtures = script.parents[2] / 'build' / 'postgresql-fixtures'
    socket = len(str(fixtures / ('x' * 12) / 's' / '.s.PGSQL.5432').encode())
    (test.assertLessEqual if short else test.assertGreater)(socket, launcher.SOCKET_PATH_LIMIT)
    if owner is not None:
        for path in [root, *root.rglob('*')]:
            os.chown(path, owner, -1)
    return root, script, fixtures


def launch(script, command, *options, run_as=RUN_AS, env=None, stderr=subprocess.PIPE, ignore=(), interpreter=sys.executable,
           user=None, wrapper=(), command_line=None):
    """Start a launcher in its own process group, with SIGINT, SIGTERM and SIGHUP at their defaults
    (or ignored, for those in ``ignore``) whatever this test process inherited. The launcher runs
    ``interpreter -c command``, or command_line if given."""
    def dispositions():
        for signum in SIGNALS:
            signal.signal(signum, signal.SIG_IGN if signum in ignore else signal.SIG_DFL)
    argv = [interpreter, *wrapper, str(script), *run_as, '--discard', *options, '--', *(command_line or [interpreter, '-c', command])]
    if user:
        argv = ['runuser', '-u', user, '--'] + argv
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=stderr, text=True, env=env, start_new_session=True,
                            preexec_fn=dispositions)


def read_until(lines, prefix, seen):
    for line in lines:
        seen.append(line)
        if line.startswith(prefix):
            return line.strip()
    return None


def finish(process, timeout=180):
    """Wait for the launcher, then return the rest of its output. The rest is read through the same
    buffered streams the checks read lines from (communicate() would skip what they buffered)."""
    try:
        process.wait(timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    output = process.stdout.read() if process.stdout else ''
    errors = process.stderr.read() if process.stderr else ''
    for stream in (process.stdout, process.stderr):
        if stream:
            stream.close()
    return output, errors


def buffered_environment():
    """This environment with stderr buffered, as an ordinary shell gives it (PYTHONUNBUFFERED makes it unbuffered)."""
    return {name: value for name, value in os.environ.items() if name != 'PYTHONUNBUFFERED'}


def full_pipe(test):
    """A pipe whose write end is non-blocking and full, as a parent that shares its non-blocking stderr would leave
    it. Returns the reader (closed after the test), the writer and how many bytes fill it."""
    reader, writer = os.pipe()
    test.addCleanup(os.close, reader)
    os.set_blocking(writer, False)
    filled = 0
    try:
        while True:
            filled += os.write(writer, b'x' * 4096)
    except BlockingIOError:
        pass
    return reader, writer, filled


def other_pythons():
    """Every other CPython 3 in /usr/bin and /usr/local/bin, one per version: {(3, 12): path, ...}."""
    found = {}
    for directory in ('/usr/bin', '/usr/local/bin'):
        for path in sorted(Path(directory).glob('python3.*')):
            match = re.fullmatch(r'python3\.(\d+)', path.name)
            version = (3, int(match.group(1))) if match else None
            if version and version != sys.version_info[:2] and version not in found and os.access(path, os.X_OK):
                found[version] = path
    return found


class VersionChoiceTests(unittest.TestCase):
    def test_the_highest_installed_version_number_is_chosen(self):
        root = Path(tempfile.mkdtemp(prefix='versions-'))
        self.addCleanup(shutil.rmtree, root)
        for version in ('9.6', '16', '10'):
            (root / version / 'bin').mkdir(parents=True)
            (root / version / 'bin' / 'pg_ctl').touch()
        with patch('shutil.which', return_value=None):
            self.assertEqual(root / '16' / 'bin', launcher.find_bindir(root=root))


class CleanupHelperTests(unittest.TestCase):
    def test_a_missing_pg_ctl_means_no_server_is_running(self):
        info = dict(bindir='/nonexistent-postgresql-bindir', data='/nonexistent-postgresql-data', run_as=None)
        self.assertFalse(launcher.running(info))

    def test_a_status_line_survives_a_closed_stream(self):
        for failure in (BrokenPipeError(32, 'Broken pipe'), OSError(5, 'Input/output error')):
            with self.subTest(failure=type(failure).__name__):
                silenced = []
                with patch('builtins.print', side_effect=failure), patch.object(launcher, 'silence_stderr', lambda: silenced.append(1)):
                    launcher.say('Throwaway PostgreSQL stopped and exited: yes')
                self.assertEqual([1], silenced, 'stderr was not pointed at /dev/null')

    def test_the_status_check_runs_in_its_own_session(self):
        seen = {}
        def status(command, **options):
            seen.update(options)
            return subprocess.CompletedProcess(command, 3)
        with patch.object(launcher.subprocess, 'run', status):
            self.assertFalse(launcher.running(dict(bindir='/synthetic/bin', data='/synthetic/data', run_as=None)))
        self.assertIs(True, seen.get('start_new_session'))

    def test_a_failed_stop_is_reported_once_retried_in_its_own_session_and_the_cleanup_goes_on(self):
        states, sessions, said = iter([True, True, True, False]), [], []
        def stop(command, stdin=None, detached=False):
            sessions.append(detached)
            raise RuntimeError('Command failed (1): pg_ctl stop\npg_ctl: cannot stop server; single-user server is running')
        with patch.object(launcher, 'running', lambda info: next(states)), patch.object(launcher, 'run', stop), \
                patch.object(launcher, 'leftover_processes', lambda info: []), patch.object(launcher, 'say', said.append), \
                patch.object(launcher.time, 'sleep', lambda seconds: None):
            self.assertEqual([], launcher.stop_server(dict(bindir='/synthetic/bin', data='/synthetic/data', run_as=None)))
        self.assertEqual([True, True], sessions)
        self.assertEqual(1, len(said))
        self.assertIn('single-user server is running', said[0])

    @unittest.skipUnless(os.path.exists('/proc/self/environ'), 'needs Linux /proc')
    def test_a_process_with_the_data_directory_as_pgdata_is_left_over_and_no_other(self):
        data = '/synthetic/postgresql-fixtures/%d/data' % os.getpid()
        variants = {'PGDATA is the data directory': ({'PGDATA': data}, True),
                    'PGDATA is a directory that only starts the same': ({'PGDATA': data + '-other'}, False),
                    'another variable names it': ({'NOT_PGDATA': data}, False)}
        base, pids = {name: value for name, value in os.environ.items() if name != 'PGDATA'}, {}
        for name, (extra, _) in variants.items():
            process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE, env={**base, **extra})
            self.addCleanup(process.wait)
            self.addCleanup(process.stdin.close)
            pids[name] = process.pid
        found = launcher.leftover_processes(dict(data=data))
        for name, (_, left) in variants.items():
            with self.subTest(name):
                self.assertIs(left, pids[name] in found)

    @unittest.skipUnless(os.path.exists('/proc/self/cwd'), 'needs Linux /proc')
    def test_a_process_working_in_the_data_directory_is_left_over_and_no_other(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-cwd-'))
        self.addCleanup(shutil.rmtree, root)
        (root / 'data').mkdir()
        (root / 'data-other').mkdir()
        (root / 'link').symlink_to(root / 'data')  # cluster.json may name the directory through a symlink
        pids = {}
        for name in ('data', 'data-other'):
            process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE, cwd=root / name,
                                       env={key: value for key, value in os.environ.items() if key != 'PGDATA'})
            self.addCleanup(process.wait)
            self.addCleanup(process.stdin.close)
            pids[name] = process.pid
        for named in ('data', 'link'):
            with self.subTest(cluster_json_names=named):
                found = launcher.leftover_processes(dict(data=str(root / named)))
                self.assertIn(pids['data'], found, 'a process working in the data directory was overlooked')
                self.assertNotIn(pids['data-other'], found, 'a directory that only starts the same was taken for it')

    def test_the_cleanup_waits_for_processes_that_still_name_the_cluster(self):
        left, slept = iter([[4242], [4242], []]), []
        with patch.object(launcher, 'running', lambda info: False), patch.object(launcher, 'leftover_processes', lambda info: next(left)), \
                patch.object(launcher.time, 'sleep', slept.append):
            self.assertEqual([], launcher.stop_server(dict(bindir='/synthetic/bin', data='/synthetic/data', run_as=None)))
        self.assertEqual(2, len(slept))


def restore_signals(test):
    """Put SIGNALS and SIGALRM back as they are now once the test is over, and HELD as it was."""
    for signum in SIGNALS + (signal.SIGALRM,):
        test.addCleanup(signal.signal, signum, signal.getsignal(signum))
    test.addCleanup(launcher.HELD.update, dict(launcher.HELD))


def interrupt(signum, frame):
    raise KeyboardInterrupt(signum)


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class SignalTests(unittest.TestCase):
    def test_the_handler_stops_the_run_and_ignores_further_signals(self):
        restore_signals(self)
        for signum in SIGNALS:
            signal.signal(signum, launcher.interrupted)
        launcher.HELD.update(starting=False, signum=None)
        with self.assertRaises(KeyboardInterrupt) as stop:
            launcher.interrupted(signal.SIGTERM, None)
        self.assertEqual((signal.SIGTERM,), stop.exception.args)
        self.assertEqual([signal.SIG_IGN] * len(SIGNALS), [signal.getsignal(signum) for signum in SIGNALS])

    def test_while_the_command_starts_the_handler_holds_the_signal_and_ignores_nothing(self):
        restore_signals(self)
        for signum in SIGNALS:
            signal.signal(signum, launcher.interrupted)
        launcher.HELD.update(starting=True, signum=None)
        self.assertIsNone(launcher.interrupted(signal.SIGHUP, None))
        self.assertEqual(signal.SIGHUP, launcher.HELD['signum'])
        # Ignored now, the signals would stay ignored in the command, which could then not be stopped.
        self.assertEqual([launcher.interrupted] * len(SIGNALS), [signal.getsignal(signum) for signum in SIGNALS])

    def test_of_two_signals_held_while_a_process_starts_the_first_decides(self):
        restore_signals(self)  # release() ignores the signals from then on
        for first, second in ((signal.SIGTERM, signal.SIGHUP), (signal.SIGHUP, signal.SIGTERM)):
            with self.subTest(first=first.name, second=second.name):
                launcher.HELD.update(starting=True, signum=None)
                launcher.interrupted(first, None)
                launcher.interrupted(second, None)
                # As when none is held: the first stops the run, and its number is the one reported (128+N).
                with self.assertRaises(KeyboardInterrupt) as stop:
                    launcher.release()
                self.assertEqual((first,), stop.exception.args)

    def test_a_status_line_nobody_reads_leaves_the_exit_status_alone(self):
        import pty  # POSIX only, like the termios module it needs
        for stream, buffered in itertools.product(('pipe', 'terminal'), (True, False)):
            with self.subTest(stream=stream, buffered=buffered):
                environment = {name: value for name, value in os.environ.items() if name != 'PYTHONUNBUFFERED'}
                if not buffered:
                    environment['PYTHONUNBUFFERED'] = '1'
                reader, writer = pty.openpty() if stream == 'terminal' else os.pipe()
                process = subprocess.Popen([sys.executable, '-c', CLOSED_STREAM, str(HERE)], stdin=subprocess.PIPE, stderr=writer,
                                           env=environment, text=True)
                os.close(writer)
                os.close(reader)  # nobody reads its stderr any more
                process.stdin.write('go\n')
                process.stdin.close()
                # Python's final flush of a buffered stderr would fail, and the exit status would be 120.
                self.assertEqual(143, process.wait(60))

    def test_an_error_before_the_first_status_line_keeps_its_exit_status_when_nobody_reads_stderr(self):
        import pty  # POSIX only, like the termios module it needs
        failures = (('usage error', [str(HERE / 'postgresql_throwaway.py'), '--discard'], 2),
                    # Reported by argparse itself, from inside parse_args().
                    ('invalid option', [str(HERE / 'postgresql_throwaway.py'), '--locale', 'synthetic-bogus', '--discard', '--', 'true'], 2),
                    ('error message', ['-c', EARLY_ERROR, str(HERE), 'message', *RUN_AS, '--discard', '--', 'true'], 1),
                    ('uncaught exception', ['-c', EARLY_ERROR, str(HERE), 'exception', *RUN_AS, '--discard', '--', 'true'], 1))
        for (name, argv, expected), stream, buffered in itertools.product(failures, ('pipe', 'terminal'), (True, False)):
            with self.subTest(name, stream=stream, buffered=buffered):
                environment = {key: value for key, value in os.environ.items() if key != 'PYTHONUNBUFFERED'}
                if not buffered:
                    environment['PYTHONUNBUFFERED'] = '1'
                reader, writer = pty.openpty() if stream == 'terminal' else os.pipe()
                os.close(reader)  # nobody reads its stderr
                process = subprocess.Popen([sys.executable, *argv], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=writer,
                                           env=environment)
                os.close(writer)
                # Not 120: the usage message or traceback is still buffered when Python's final flush fails.
                self.assertEqual(expected, process.wait(60))

    def test_a_signal_while_the_exit_waits_for_a_full_stderr_keeps_an_early_errors_exit_status(self):
        failures = (('invalid option', 'usage', ['--locale', 'synthetic-bogus'], 2), ('error message', 'message', RUN_AS, 1),
                    ('uncaught exception', 'exception', RUN_AS, 1))
        for name, failure, options, expected in failures:
            with self.subTest(name):
                _, writer, _ = full_pipe(self)  # and nobody drains it
                process = subprocess.Popen([sys.executable, '-c', EARLY_ERROR, str(HERE), failure, *options, '--discard', '--', 'true'],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=writer, env=buffered_environment(),
                                           text=True)
                os.close(writer)
                self.addCleanup(process.stdout.close)
                self.addCleanup(process.kill)
                self.assertTrue(select.select([process.stdout], [], [], 30)[0], 'the launcher did not fail')
                self.assertEqual('failed', process.stdout.readline().strip())
                time.sleep(0.5)  # well inside EXIT_FLUSH_GRACE: the exit is waiting for the full stderr
                process.send_signal(signal.SIGINT)  # Ctrl-C: main() never got to install its handlers
                # Not 120: the wait was cut short, so stderr was silenced before Python's final flush.
                self.assertEqual(expected, process.wait(60))

    def test_a_full_non_blocking_stderr_is_not_silenced_and_buffered_keeps_the_line_that_met_it(self):
        for buffered in (True, False):
            with self.subTest(buffered=buffered):
                environment = buffered_environment()
                if not buffered:
                    environment['PYTHONUNBUFFERED'] = '1'
                reader, writer, filled = full_pipe(self)
                process = subprocess.Popen([sys.executable, '-c', FULL_STREAM, str(HERE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=writer, env=environment, text=True)
                os.close(writer)
                self.addCleanup(process.kill)
                # A launcher that waited for the reader here would never say this, as the pipe is drained only after it.
                self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'blocked on its full stderr')
                self.assertEqual('said', process.stdout.readline().strip())  # its first status line met the full pipe
                drained = 0
                while drained < filled:
                    drained += len(os.read(reader, filled - drained))
                process.stdin.write('go\n')
                process.stdin.close()
                process.stdout.read()
                process.stdout.close()
                self.assertEqual(0, process.wait(60))
                text = b''
                for chunk in iter(lambda: os.read(reader, 65536), b''):
                    text += chunk
                # The reader is still there, so nothing may silence stderr. Unbuffered, Python itself drops line 1.
                expected = ['status line 1'] * buffered + ['status line 2', 'status line 3', 'command stderr line']
                self.assertEqual(expected, text.decode().splitlines())

    def test_at_exit_a_full_non_blocking_stderr_gets_time_to_take_the_last_line(self):
        reader, writer, filled = full_pipe(self)
        process = subprocess.Popen([sys.executable, '-c', FULL_AT_EXIT, str(HERE)], stdout=subprocess.PIPE, stderr=writer,
                                   env=buffered_environment(), text=True)  # unbuffered, the line would be dropped at once
        os.close(writer)
        self.addCleanup(process.kill)
        self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'blocked on its full stderr')
        self.assertEqual('exiting', process.stdout.readline().strip())
        time.sleep(0.3)  # the reader is slow, not gone: it drains the pipe well within EXIT_FLUSH_GRACE
        drained = 0
        while drained < filled:
            drained += len(os.read(reader, filled - drained))
        self.assertEqual(0, process.wait(60))
        process.stdout.close()
        text = b''
        for chunk in iter(lambda: os.read(reader, 65536), b''):
            text += chunk
        self.assertEqual('last status line\n', text.decode())

    def test_at_exit_a_stderr_still_full_after_the_grace_is_silenced_and_the_exit_status_kept(self):
        _, writer, _ = full_pipe(self)  # and nobody drains it
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, '-c', FULL_AT_EXIT, str(HERE)], stdout=subprocess.DEVNULL, stderr=writer,
                                   env=buffered_environment())
        os.close(writer)
        self.addCleanup(process.kill)
        # Not 120: after EXIT_FLUSH_GRACE the line is given up and stderr silenced before Python's final flush.
        self.assertEqual(0, process.wait(60))
        self.assertGreaterEqual(time.monotonic() - started, launcher.EXIT_FLUSH_GRACE, 'the exit did not wait for the reader')

    def test_a_signal_held_while_a_start_fails_stops_the_run_instead_of_the_failure(self):
        restore_signals(self)
        for signum in SIGNALS:
            signal.signal(signum, launcher.interrupted)
        launcher.HELD.update(starting=False, signum=None)

        class Signalled(subprocess.Popen):
            def __init__(self, *arguments, **options):
                os.kill(os.getpid(), signal.SIGTERM)  # held: no process is known yet
                super().__init__(*arguments, **options)
        with patch.object(launcher.subprocess, 'Popen', Signalled), self.assertRaises(KeyboardInterrupt) as stop:
            launcher.start(['/nonexistent-synthetic-command'])
        self.assertEqual((signal.SIGTERM,), stop.exception.args)
        self.assertFalse(launcher.HELD['starting'])

    def test_a_step_signalled_just_as_its_popen_returns_is_still_supervised(self):
        restore_signals(self)
        for signum in SIGNALS:
            signal.signal(signum, launcher.interrupted)
        launcher.HELD.update(starting=False, signum=None)
        started = []

        class Signalled(subprocess.Popen):
            def __init__(self, *arguments, **options):
                super().__init__(*arguments, **options)
                started.append(self)
                os.kill(os.getpid(), signal.SIGTERM)  # handled before run() has bound the process
        self.addCleanup(lambda: [process.kill() for process in started if process.poll() is None])
        step = [sys.executable, '-c', 'import sys; sys.stdin.read()']  # like tee: it ends only when its input ends
        with patch.object(launcher.subprocess, 'Popen', Signalled), self.assertRaises(KeyboardInterrupt):
            launcher.run(step, stdin='synthetic settings\n')
        self.assertEqual(1, len(started))
        # Supervised: its input was closed and it was waited for, so it ended; unsupervised it would wait for more.
        self.assertEqual(0, started[0].returncode, 'the step was left waiting for its input')


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class StepTests(unittest.TestCase):
    """An interrupted setup step that outlives STEP_GRACE is stopped, and the wait for it stays bounded."""
    def interrupted_run(self, command, step_grace=0.3, term_grace=1):
        """run() with a SIGTERM to this process 0.5 s in, while the step runs, handled by the launcher's own
        handler: so a run() that still held signals while it waited for the step would wait it out."""
        restore_signals(self)
        launcher.HELD.update(starting=False, signum=None)
        signal.signal(signal.SIGTERM, launcher.interrupted)
        signal.signal(signal.SIGALRM, lambda signum, frame: os.kill(os.getpid(), signal.SIGTERM))
        started = time.monotonic()
        with patch.object(launcher, 'STEP_GRACE', step_grace), patch.object(launcher, 'TERM_GRACE', term_grace):
            signal.setitimer(signal.ITIMER_REAL, 0.5)  # the step is running by then
            try:
                with self.assertRaises(KeyboardInterrupt):
                    launcher.run(command)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
        return time.monotonic() - started

    def pids(self, path):
        deadline = time.monotonic() + 10
        while not (path.exists() and path.read_text().endswith('\n')) and time.monotonic() < deadline:
            time.sleep(0.01)
        return [int(pid) for pid in path.read_text().split()]

    def gone(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    def kill_later(self, pids=None, marker=None):
        """Once the test is over, SIGKILL what the pids file names and every process whose command line
        holds the marker. Registered before the run, so a check that fails early leaves no step behind."""
        def kill():
            found = set()
            if pids is not None and pids.exists():
                found.update(int(pid) for pid in pids.read_text().split())
            if marker is not None:
                found.update(int(entry.name) for entry in Path('/proc').glob('[0-9]*') if marker.encode() in _cmdline(entry))
            for pid in found:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        self.addCleanup(kill)

    def scratch(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-step-'))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def test_a_step_that_ignores_sigterm_is_killed(self):
        pids = self.scratch() / 'pids'
        step = ("import os, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "open(%r, 'w').write('%%d\\n' %% os.getpid())\ntime.sleep(30)\n" % str(pids))
        self.kill_later(pids, marker=str(pids))
        elapsed = self.interrupted_run([sys.executable, '-c', step])
        self.assertLess(elapsed, 15, 'run() waited for the step instead of stopping it')
        self.assertTrue(self.gone(self.pids(pids)[0]), 'the step was left running')

    def test_a_step_that_finishes_within_the_grace_is_left_to_finish(self):
        done = self.scratch() / 'done'
        step = "import time; time.sleep(1.5); open(%r, 'w').write('finished')" % str(done)
        self.kill_later(marker=str(done))
        elapsed = self.interrupted_run([sys.executable, '-c', step], step_grace=launcher.STEP_GRACE, term_grace=launcher.TERM_GRACE)
        self.assertTrue(done.exists(), 'the interrupted step was stopped instead of left to finish')
        self.assertLess(elapsed, 15)

    def test_the_wait_ends_even_when_another_process_still_holds_the_steps_output(self):
        pids = self.scratch() / 'pids'
        holder = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)  # " + str(pids)
        step = ("import os, signal, subprocess, sys, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "holder = subprocess.Popen([sys.executable, '-c', %r])\n"
                "open(%r, 'w').write('%%d %%d\\n' %% (os.getpid(), holder.pid))\ntime.sleep(30)\n" % (holder, str(pids)))
        self.kill_later(pids, marker=str(pids))
        elapsed = self.interrupted_run([sys.executable, '-c', step])
        step_pid, holder_pid = self.pids(pids)
        self.assertLess(elapsed, 15, 'run() kept reading output that another process still held')
        self.assertTrue(self.gone(step_pid), 'the step was left running')

    @unittest.skipUnless(ROOT and sys.platform.startswith('linux') and os.path.exists('/proc/self/cmdline') and shutil.which('runuser'),
                         'needs Linux with /proc, root and runuser')
    def test_as_root_runuser_stops_the_step_itself(self):
        interpreter = os.path.realpath(sys.executable)
        if subprocess.run(['runuser', '-u', 'postgres', '--', 'test', '-x', interpreter]).returncode:
            self.skipTest('the postgres account cannot run %s' % interpreter)
        marker = 'launcher-step-under-runuser-%d' % os.getpid()
        step = 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)  # ' + marker
        self.kill_later(marker=marker)
        # runuser kills its child 2 s after a SIGTERM; a SIGKILL any earlier would end runuser alone, so the
        # launcher's own TERM_GRACE must leave it that long.
        elapsed = self.interrupted_run(['runuser', '-u', 'postgres', '--', interpreter, '-c', step], term_grace=launcher.TERM_GRACE)
        left = [entry.name for entry in Path('/proc').glob('[0-9]*') if marker.encode() in _cmdline(entry)]
        self.assertLess(elapsed, 15, 'run() waited for the step under runuser')
        self.assertEqual([], left, 'the step under runuser was left running')


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class SetupTests(unittest.TestCase):
    def long_cluster(self):
        """A cluster path too long for an in-cluster socket, with SHORT_SOCKET_BASE in a temporary root."""
        root = Path(tempfile.mkdtemp(prefix='launcher-socket-'))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        base = root / 'short'
        base.mkdir()
        cluster = root / ('c' * 90) / 'postgresql-fixtures' / 'synthetic'
        self.assertGreater(len(str(cluster / 's' / '.s.PGSQL.5432').encode()), launcher.SOCKET_PATH_LIMIT)
        for target, value in (('SHORT_SOCKET_BASE', str(base)), ('run', lambda command, stdin=None, detached=False: '')):
            patcher = patch.object(launcher, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return cluster, base

    def test_a_signal_while_the_short_socket_directory_is_made_waits_until_cluster_json_names_it(self):
        restore_signals(self)
        mkdtemp = tempfile.mkdtemp
        for signum in SIGNALS:
            with self.subTest(signal=signum.name):
                cluster, base = self.long_cluster()
                signal.signal(signum, interrupt)
                made = []

                def signalled(*arguments, **options):
                    made.append(mkdtemp(*arguments, **options))
                    os.kill(os.getpid(), signum)
                    return made[-1]
                with patch.object(launcher.tempfile, 'mkdtemp', signalled), self.assertRaises(KeyboardInterrupt):
                    launcher.create(cluster, Path('/usr/lib/postgresql/16/bin'), None, 'c')
                self.assertEqual(1, len(made))
                self.assertEqual(base, Path(made[0]).parent)
                self.assertTrue((cluster / 'cluster.json').exists(), 'interrupted before cluster.json named the socket directory')
                self.assertEqual(made[0], json.loads((cluster / 'cluster.json').read_text())['sockets'])

    def test_a_cluster_json_that_cannot_be_written_takes_the_short_socket_directory_with_it(self):
        cluster, base = self.long_cluster()
        write_text = Path.write_text
        def full(path, *arguments, **options):
            if path.name == 'cluster.json':
                raise OSError(28, 'No space left on device (synthetic)')
            return write_text(path, *arguments, **options)
        with patch.object(Path, 'write_text', full), self.assertRaises(OSError):
            launcher.create(cluster, Path('/usr/lib/postgresql/16/bin'), None, 'c')
        self.assertEqual([], list(base.iterdir()), 'the temporary socket directory was left behind')

    def test_the_server_account_appends_the_settings_and_root_writes_nothing_in_its_directories(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-config-'))
        self.addCleanup(shutil.rmtree, root)
        import pwd
        account = pwd.getpwuid(os.getuid()).pw_name  # any existing account: os.chown is recorded, not run
        cluster, commands = root / 'postgresql-fixtures' / 'synthetic', []
        with patch.object(launcher, 'run', lambda command, stdin=None, detached=False: commands.append((command, stdin)) or ''), \
                patch.object(launcher.os, 'chown', lambda *arguments: None), \
                patch.object(launcher, 'SHORT_SOCKET_BASE', str(root)):  # a long TMPDIR: any short socket directory goes here
            info = launcher.create(cluster, Path('/usr/lib/postgresql/16/bin'), account, 'c')
        config = str(Path(info['data']) / 'postgresql.conf')
        appends = [(command, stdin) for command, stdin in commands if config in command]
        self.assertEqual([['runuser', '-u', account, '--', 'tee', '-a', config]], [command for command, _ in appends])
        self.assertIn("listen_addresses = ''", appends[0][1])
        self.assertFalse(Path(config).exists(), 'the launcher wrote the server configuration itself')

    def test_a_server_that_does_not_start_reports_the_end_of_its_log(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-start-'))
        self.addCleanup(shutil.rmtree, root)
        (root / 'bin').mkdir()
        (root / 'bin' / 'pg_ctl').write_text('#!/bin/sh\necho "pg_ctl: could not start server"\nexit 1\n')
        (root / 'bin' / 'pg_ctl').chmod(0o755)
        cluster = root / 'fixtures' / 'synthetic'
        cluster.mkdir(parents=True)
        log = cluster / 'server.log'
        log.write_text('\n'.join('synthetic log line %d' % number for number in range(30)) + '\nFATAL:  synthetic start failure\n')
        (cluster / 'cluster.json').write_text(json.dumps(dict(bindir=str(root / 'bin'), data=str(cluster / 'data'),
                                                              sockets=str(cluster / 's'), log=str(log), run_as=None)))
        with patch.object(launcher, 'FIXTURES', root / 'fixtures'):
            with self.assertRaises(RuntimeError) as failure:
                launcher.control(cluster, 'start')
        self.assertIn('could not start server', str(failure.exception))
        self.assertIn('FATAL:  synthetic start failure', str(failure.exception))
        self.assertNotIn('synthetic log line 5\n', str(failure.exception))


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class OtherPythonTests(unittest.TestCase):
    """The checks that need no server, under every other CPython 3 installed here. Python before 3.13 flushes a
    closed stdin in communicate() and raises ValueError, which run() must not let replace an interrupt."""
    def test_the_checks_without_a_server_pass_under_every_other_installed_python(self):
        pythons = other_pythons()
        if not pythons:
            self.skipTest('no other CPython 3 in /usr/bin or /usr/local/bin')
        names = ['tests.postgresql_launcher_checks.' + case for case in ('VersionChoiceTests', 'CleanupHelperTests', 'SignalTests',
                                                                          'StepTests', 'SetupTests')]
        for version, python in pythons.items():
            with self.subTest(python='%d.%d' % version):
                # These checks need the standard library only (-s: not even the caller's own site-packages).
                result = subprocess.run([str(python), '-s', '-m', 'unittest', *names], cwd=HERE.parent, stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, timeout=600)
                self.assertEqual(0, result.returncode, result.stderr[-4000:])


@unittest.skipUnless(sys.platform.startswith('linux') and binaries_available(), 'needs Linux and the PostgreSQL server binaries')
class LauncherChecks(unittest.TestCase):
    def launch(self, *arguments, **options):
        process = launch(*arguments, **options)
        self.addCleanup(self.end, process)  # after a failed check: stop the launcher before the copy goes
        return process

    @staticmethod
    def end(process):
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()

    def assertNothingLeft(self, root, fixtures, sockets, errors):
        self.assertIn('Throwaway PostgreSQL stopped and exited: yes', errors)
        self.assertEqual([], list(fixtures.iterdir()) if fixtures.exists() else [], 'a cluster directory was left behind')
        if sockets:
            self.assertFalse(Path(sockets).exists(), 'the temporary socket directory was left behind')
        self.assertEqual([], processes_naming(root), 'a process from the run was left running')

    def socket_directory(self, line):
        self.assertIsNotNone(line, 'the launcher never named its temporary socket directory')
        path = line.rsplit(': ', 1)[1]
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def test_a_terminated_or_hung_up_launcher_stops_the_server_and_discards_the_cluster(self):
        for signum, whole_group in ((signal.SIGTERM, True), (signal.SIGHUP, False), (signal.SIGINT, True)):
            with self.subTest(signal=signum.name):
                root, script, fixtures = private_copy(self)
                process = self.launch(script, WAITING + '# %s\n' % root)  # named, so a failed check can stop it
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                line = read_until(process.stderr, STARTED, seen)
                self.assertIsNotNone(line, 'the launcher never reported its cluster')
                cluster = Path(line.rsplit(': ', 1)[1])
                child = int(process.stdout.readline())
                (os.killpg if whole_group else os.kill)(process.pid, signum)
                try:
                    process.wait(120)
                except subprocess.TimeoutExpired:
                    pass
                left = Path('/proc/%d' % child).exists()
                if left:  # it holds the launcher's output, so reading that would wait for it
                    os.kill(child, signal.SIGKILL)
                _, errors = finish(process, timeout=120)
                self.assertEqual(128 + signum, process.returncode, errors)
                self.assertFalse(left, 'the command was left running')
                self.assertFalse(cluster.exists(), 'the cluster directory was left behind')
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_signal_while_the_command_starts_still_stops_the_command(self):
        for when, startable in (('after', True), ('before', True), ('before', False)):
            with self.subTest(signal=when + ' Popen starts the command', command='runs' if startable else 'cannot be started'):
                root, script, fixtures = private_copy(self)
                wrapper = root / 'signal_while_starting.py'
                wrapper.write_text(SIGNAL_WHILE_STARTING)
                if startable:
                    marker = 'signalled command %s' % root  # names root, so assertNothingLeft also looks for the command
                    command, command_line = 'import time  # %s\ntime.sleep(30)\n' % marker, None
                else:
                    marker = str(root / 'missing-command')
                    command, command_line = None, [marker]
                started = time.monotonic()
                process = self.launch(script, command, wrapper=[str(wrapper), when, marker], command_line=command_line)
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                _, errors = finish(process, timeout=90)
                # Also when Popen then fails: the held signal, not the failure to start, decides the exit status.
                self.assertEqual(143, process.returncode, errors)
                self.assertIn('Interrupted by signal 15', errors)
                # Not the 30 s that terminate() would wait for a command that inherited an ignored SIGTERM.
                self.assertLess(time.monotonic() - started, 25, errors)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_signal_just_as_a_setup_step_starts_still_lets_the_step_finish(self):
        for step, marker in (('tee', "'tee'"), ('initdb', "bin/initdb'")):
            for when in ('after', 'before'):
                with self.subTest(step=step, signal=when + ' Popen starts it'):
                    root, script, fixtures = private_copy(self)
                    wrapper = root / 'signal_while_starting.py'
                    wrapper.write_text(SIGNAL_WHILE_STARTING)
                    started = time.monotonic()
                    process = self.launch(script, WAITING, wrapper=[str(wrapper), when, marker])
                    seen = []
                    sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                    _, errors = finish(process, timeout=120)
                    self.assertEqual(143, process.returncode, errors)
                    # Supervised like any interrupted step: tee's input is closed, initdb finishes before the stop.
                    self.assertNotIn('Command failed', errors)
                    self.assertLess(time.monotonic() - started, 25, errors)  # not STOP_GRACE spent on a tee left waiting
                    self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_command_ended_by_a_signal_gives_128_plus_its_number(self):
        for signum in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=signum.name):
                root, script, fixtures = private_copy(self)
                process = self.launch(script, 'import os, signal; os.kill(os.getpid(), signal.%s)' % signum.name)
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                _, errors = finish(process)
                self.assertEqual(128 + signum, process.returncode, errors)
                self.assertNotIn('Interrupted by signal', errors)  # the command was, not the launcher
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_process_that_uses_the_cluster_without_naming_it_is_waited_for(self):
        for how in WAYS_TO_USE_THE_CLUSTER:
            with self.subTest(how=how):
                root, script, fixtures = private_copy(self)
                release = root / 'release'
                self.addCleanup(release.touch)  # after a failed check too: the waiter then ends before the copy goes
                process = self.launch(script, LEAVES_A_PROCESS % (str(release), how))
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                line = read_until(process.stderr, STARTED, seen)
                self.assertIsNotNone(line, 'the launcher never reported its cluster')
                data = Path(line.rsplit(': ', 1)[1]) / 'data'
                waiter = int(process.stdout.readline())
                deadline = time.monotonic() + 60
                while processes_naming(data) and time.monotonic() < deadline:  # until the cleanup has stopped the server
                    time.sleep(0.05)
                time.sleep(2)  # a cleanup that overlooked the waiter would have reported and exited by now
                self.assertTrue(Path('/proc/%d' % waiter).exists())
                self.assertIsNone(process.poll(), 'the cleanup ended while a process still used the cluster (%s)' % how)
                release.touch()
                _, errors = finish(process)
                self.assertEqual(0, process.returncode, errors)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_process_still_using_the_cluster_after_the_wait_is_reported_and_the_cluster_kept(self):
        for how in WAYS_TO_USE_THE_CLUSTER:
            with self.subTest(how=how):
                root, script, fixtures = private_copy(self)
                release = root / 'release'
                self.addCleanup(release.touch)
                wrapper = root / 'short_stop_grace.py'
                wrapper.write_text(SHORT_STOP_GRACE)
                process = self.launch(script, LEAVES_A_PROCESS % (str(release), how), wrapper=[str(wrapper), str(script.parent)])
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                line = read_until(process.stderr, STARTED, seen)
                self.assertIsNotNone(line, 'the launcher never reported its cluster')
                cluster = Path(line.rsplit(': ', 1)[1])
                waiter = int(process.stdout.readline())
                _, errors = finish(process, timeout=120)
                # The waiter outlives the 3 s wait: the cleanup must say so and must not delete what it still uses.
                self.assertEqual(1, process.returncode, errors)
                self.assertIn('Throwaway PostgreSQL stopped and exited: NO (pids [%d])' % waiter, errors)
                self.assertNotIn('Discarded', errors)
                self.assertTrue(cluster.exists(), 'the cluster was deleted while a process still used it')
                self.assertTrue(Path(sockets).exists(), 'the socket directory was removed while the cluster is kept')

    def test_the_cleanup_sees_every_process_of_the_server(self):
        root, script, fixtures = private_copy(self)
        output, errors = finish(self.launch(script, SEES_THE_SERVER % str(script.parent)))
        sockets = next((line.rsplit(': ', 1)[1] for line in errors.splitlines() if line.startswith(SOCKETS)), None)
        if sockets:
            self.addCleanup(shutil.rmtree, sockets, ignore_errors=True)
        seen = json.loads(output)
        self.assertTrue(seen['children'], 'the server had no other process (checkpointer, background writer, ...)')
        # Its children name neither the data directory nor PGDATA any more; they run in the data directory.
        self.assertEqual([], sorted(set([seen['postmaster']] + seen['children']) - set(seen['found'])), seen)
        self.assertNothingLeft(root, fixtures, sockets, errors)

    def test_a_failed_setup_leaves_nothing_behind(self):
        runs = [('as the calling account', None)]
        if ROOT:
            runs.append(('as the server account without --run-as', 'postgres'))
        for name, user in runs:
            with self.subTest(name):
                interpreter = os.path.realpath(sys.executable) if user else sys.executable
                if user and subprocess.run(['runuser', '-u', user, '--', 'test', '-x', interpreter]).returncode:
                    self.skipTest('%s cannot run %s' % (user, interpreter))
                root, script, fixtures = private_copy(self, owner=server_uid() if user else None)
                process = self.launch(script, 'pass', '--bindir', '/nonexistent-postgresql-bindir',
                                 run_as=[] if user else RUN_AS, interpreter=interpreter, user=user)
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                _, errors = finish(process)
                self.assertNotEqual(0, process.returncode)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_closed_terminal_or_broken_pipe_neither_stops_the_cleanup_nor_changes_the_exit_status(self):
        import pty  # POSIX only, like the termios module it needs
        variants = (('pipe', signal.SIGTERM), ('terminal', signal.SIGHUP), ('pipe, the command succeeds', None))
        for (variant, signum), buffered in itertools.product(variants, (True, False)):
            # Buffered is stderr as an ordinary shell gives it; PYTHONUNBUFFERED=1 makes it unbuffered.
            with self.subTest(variant, buffered=buffered):
                root, script, fixtures = private_copy(self)
                environment = {name: value for name, value in os.environ.items() if name != 'PYTHONUNBUFFERED'}
                if not buffered:
                    environment['PYTHONUNBUFFERED'] = '1'
                go = root / 'go'
                reader, writer = pty.openpty() if variant == 'terminal' else os.pipe()
                process = self.launch(script, WAITING if signum else UNTIL % str(go), stderr=writer, env=environment)
                os.close(writer)
                try:
                    text, deadline = '', time.monotonic() + 120
                    while STARTED not in text and time.monotonic() < deadline:
                        if select.select([reader], [], [], 1)[0]:
                            text += os.read(reader, 65536).decode(errors='replace')
                finally:
                    os.close(reader)  # nobody reads the launcher's stderr any more
                self.assertIn(STARTED, text)
                sockets = self.socket_directory(next((line.strip() for line in text.splitlines() if line.startswith(SOCKETS)), None))
                if signum:
                    child = int(process.stdout.readline())
                    os.killpg(process.pid, signum)
                else:
                    child = None
                    go.touch()  # the command finishes; every later status line meets the closed stream
                self.assertEqual(128 + signum if signum else 0, process.wait(180))
                process.stdout.close()
                self.assertEqual([], list(fixtures.iterdir()), 'a cluster directory was left behind')
                self.assertFalse(Path(sockets).exists(), 'the temporary socket directory was left behind')
                self.assertEqual([], processes_naming(root))
                if child:
                    self.assertFalse(Path('/proc/%d' % child).exists(), 'the command was left running')

    def test_a_signal_during_initdb_leaves_nothing_behind(self):
        for signum, whole_group in ((signal.SIGTERM, False), (signal.SIGHUP, False), (signal.SIGINT, True)):
            with self.subTest(signal=signum.name, whole_group=whole_group):
                root, script, fixtures = private_copy(self)
                process = self.launch(script, WAITING)
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                deadline, seen_backend = time.monotonic() + 60, False
                while not seen_backend and process.poll() is None and time.monotonic() < deadline:
                    seen_backend = initdb_runs_a_backend(root)
                    time.sleep(0 if seen_backend else 0.002)
                self.assertTrue(seen_backend, 'initdb was never seen running its backend')
                (os.killpg if whole_group else os.kill)(process.pid, signum)
                _, errors = finish(process)
                self.assertEqual(128 + signum, process.returncode, errors)
                # run() waited for initdb (StepTests pin that a step within STEP_GRACE is left to finish), so the
                # cleanup's stop never met its single-user backend.
                self.assertNotIn('Command failed', errors)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_repeated_group_signal_does_not_cut_the_cleanup_short(self):
        root, script, fixtures = private_copy(self)
        process = self.launch(script, WAITING)
        seen = []
        sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
        self.assertIsNotNone(read_until(process.stderr, STARTED, seen))
        int(process.stdout.readline())
        os.killpg(process.pid, signal.SIGTERM)
        self.assertIsNotNone(read_until(process.stderr, 'Interrupted by signal', seen))
        deadline = time.monotonic() + 120
        while process.poll() is None and time.monotonic() < deadline:  # every 20 ms until the cleanup is over
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        _, errors = finish(process)
        self.assertEqual(143, process.returncode, errors)
        # The cleanup's stop ran in its own session: no repeated SIGTERM made runuser kill it.
        self.assertNotIn('Command failed', errors)
        self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_signal_during_the_cleanup_after_the_command_succeeded_is_ignored(self):
        for whole_group in (False, True):
            with self.subTest(whole_group=whole_group):
                root, script, fixtures = private_copy(self)
                process = self.launch(script, 'pass')
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                deadline, stopping = time.monotonic() + 60, False
                while not stopping and process.poll() is None and time.monotonic() < deadline:
                    stopping = bool(processes_naming(root, b'\0stop\0'))  # the cleanup's pg_ctl stop
                    time.sleep(0 if stopping else 0.002)
                self.assertTrue(stopping, 'the cleanup was never seen stopping the server')
                try:
                    (os.killpg if whole_group else os.kill)(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                _, errors = finish(process)
                self.assertEqual(0, process.returncode, errors)
                self.assertNotIn('Command failed', errors)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_hangup_ignored_at_start_stays_ignored_for_the_whole_run(self):
        root, script, fixtures = private_copy(self)
        process = self.launch(script, HANGUP_PROBE, ignore=(signal.SIGHUP,))
        seen = []
        sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
        self.assertEqual('SIGHUP ignored: True', process.stdout.readline().strip())
        os.killpg(process.pid, signal.SIGHUP)  # as when the terminal of a nohup run closes
        output, errors = finish(process)
        self.assertEqual(0, process.returncode, errors)
        self.assertIn('finished normally', output)
        self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_libpq_variables_never_reach_a_client_and_only_the_server_account_owns_its_files(self):
        hostile = dict(os.environ, PGHOSTADDR='192.0.2.1', PGPORT='1', PGSERVICE='synthetic-missing-service')
        for layout in ('deep', 'short'):
            with self.subTest(checkout=layout):
                root, script, fixtures = private_copy(self, short=layout == 'short')
                output, errors = finish(self.launch(script, PROBE, env=hostile))
                sockets = next((line.rsplit(': ', 1)[1] for line in errors.splitlines() if line.startswith(SOCKETS)), None)
                if sockets:
                    self.addCleanup(shutil.rmtree, sockets, ignore_errors=True)
                self.assertNothingLeft(root, fixtures, sockets, errors)
                seen = json.loads(output)
                self.assertEqual([], seen['libpq'])
                self.assertTrue(seen['dsn'].startswith('host=/'), seen['dsn'])
                if layout == 'deep':  # too long for a socket in the cluster: a private directory under /tmp
                    self.assertEqual(Path('/tmp'), Path(seen['sockets']).parent)
                    self.assertEqual(sockets, seen['sockets'], 'the socket directory was not named on stderr')
                else:  # <cluster>/s
                    self.assertEqual(fixtures, Path(seen['sockets']).parent.parent)
                    self.assertIsNone(sockets, 'a socket directory inside the cluster was announced as temporary')
                self.assertEqual([0o700, server_uid()], seen['socket_dir'])
                if RUN_AS:
                    server = server_uid()
                    self.assertEqual({'cluster': 0, 'metadata': 0, 'data': server, 'config': server, 'log': server}, seen['owners'])

    def test_a_deep_checkout_gets_a_private_socket_directory_under_tmp_whatever_tmpdir_says(self):
        launcher.FIXTURES.parent.mkdir(parents=True, exist_ok=True)  # build/ is gitignored, so a fresh clone has none
        scratch = Path(tempfile.mkdtemp(prefix='launcher-tmpdir-', dir=launcher.FIXTURES.parent))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        closed = scratch / 'closed'
        closed.mkdir(mode=0o700)  # like a per-user TMPDIR (pam_tmpdir): the server account cannot enter it
        long = scratch / ('t' * 90)
        long.mkdir()
        for name, tmpdir in (('unset', None), ('closed to the server account', closed), ('long', long)):
            with self.subTest(TMPDIR=name):
                root, script, fixtures = private_copy(self)
                environment = {key: value for key, value in os.environ.items() if key not in ('TMPDIR', 'TEMP', 'TMP')}
                if tmpdir:
                    environment['TMPDIR'] = str(tmpdir)
                output, errors = finish(self.launch(script, PROBE, env=environment))
                self.assertIn('Throwaway PostgreSQL stopped and exited: yes', errors)
                seen = json.loads(output)
                sockets = Path(seen['sockets'])
                self.addCleanup(shutil.rmtree, sockets, ignore_errors=True)
                self.assertEqual(Path('/tmp'), sockets.parent)
                self.assertTrue(sockets.name.startswith('mhpg-'))
                self.assertEqual([0o700, server_uid()], seen['socket_dir'])
                self.assertNothingLeft(root, fixtures, str(sockets), errors)


if __name__ == '__main__':
    unittest.main()
