"""Opt-in checks of the throwaway PostgreSQL launcher itself. Fictional data only.

Not collected by ``unittest discover``. The launch checks need Linux and the
PostgreSQL server binaries; as root, the launcher's server runs as the postgres
account, and the failed-setup check also runs once as that account without
--run-as. Most launch checks run a private copy of the launcher in a unique
directory under build/ (deep enough that its socket goes in a private directory
under /tmp), with a small probe command and --discard, so every cluster is
removed again and concurrent runs cannot disturb each other. The other checks
need only POSIX. From candidates/full-stack/backend:

    python -m unittest tests.postgresql_launcher_checks -v
"""
import json
import os
from pathlib import Path
import pty
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


def private_copy(test, owner=None):
    """A launcher copy in a unique directory under build/, removed after the test. Its in-cluster
    socket path is longer than a Unix socket allows."""
    launcher.FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='launcher-check-', dir=launcher.FIXTURES.parent))
    test.addCleanup(remove_copy, root)
    root.chmod(0o755)  # the server account must reach data/ inside it
    script = root / ('deep-checkout-' + 'd' * 16) / 'backend' / 'tests' / 'postgresql_throwaway.py'
    script.parent.mkdir(parents=True)
    shutil.copy(launcher.__file__, script)
    fixtures = script.parents[2] / 'build' / 'postgresql-fixtures'
    test.assertGreater(len(str(fixtures / ('x' * 12) / 's' / '.s.PGSQL.5432').encode()), launcher.SOCKET_PATH_LIMIT)
    if owner is not None:
        for path in [root, *root.rglob('*')]:
            os.chown(path, owner, -1)
    return root, script, fixtures


def launch(script, command, *options, run_as=RUN_AS, env=None, stderr=subprocess.PIPE, ignore=(), interpreter=sys.executable, user=None):
    """Start a launcher in its own process group, with SIGINT, SIGTERM and SIGHUP at their defaults
    (or ignored, for those in ``ignore``) whatever this test process inherited."""
    def dispositions():
        for signum in SIGNALS:
            signal.signal(signum, signal.SIG_IGN if signum in ignore else signal.SIG_DFL)
    argv = [interpreter, str(script), *run_as, '--discard', *options, '--', interpreter, '-c', command]
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
                with patch('builtins.print', side_effect=failure):
                    launcher.say('Throwaway PostgreSQL stopped and exited: yes')

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

    def test_the_cleanup_waits_for_processes_that_still_name_the_cluster(self):
        left, slept = iter([[4242], [4242], []]), []
        with patch.object(launcher, 'running', lambda info: False), patch.object(launcher, 'leftover_processes', lambda info: next(left)), \
                patch.object(launcher.time, 'sleep', slept.append):
            self.assertEqual([], launcher.stop_server(dict(bindir='/synthetic/bin', data='/synthetic/data', run_as=None)))
        self.assertEqual(2, len(slept))


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class SetupTests(unittest.TestCase):
    def test_the_server_account_appends_the_settings_and_root_writes_nothing_in_its_directories(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-config-'))
        self.addCleanup(shutil.rmtree, root)
        import pwd
        account = pwd.getpwuid(os.getuid()).pw_name  # any existing account: os.chown is recorded, not run
        cluster, commands = root / 'postgresql-fixtures' / 'synthetic', []
        with patch.object(launcher, 'run', lambda command, stdin=None, detached=False: commands.append((command, stdin)) or ''), \
                patch.object(launcher.os, 'chown', lambda *arguments: None):
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
                process = self.launch(launcher.__file__, WAITING)
                try:
                    line = read_until(process.stderr, STARTED, [])
                    self.assertIsNotNone(line, 'the launcher never reported its cluster')
                    cluster = Path(line.rsplit(': ', 1)[1])
                    child = int(process.stdout.readline())
                    (os.killpg if whole_group else os.kill)(process.pid, signum)
                    self.assertEqual(128 + signum, process.wait(120))
                    report = process.stderr.read()
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    process.stdout.close()
                    process.stderr.close()
                self.assertIn('Throwaway PostgreSQL stopped and exited: yes', report)
                self.assertFalse(cluster.exists(), 'the cluster directory was left behind')
                self.assertEqual([], processes_naming(cluster / 'data'))
                self.assertFalse(Path('/proc/%d' % child).exists(), 'the command was left running')

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

    def test_a_closed_terminal_or_broken_pipe_does_not_stop_the_cleanup(self):
        for variant, signum in (('pipe', signal.SIGTERM), ('terminal', signal.SIGHUP)):
            with self.subTest(variant):
                root, script, fixtures = private_copy(self)
                reader, writer = pty.openpty() if variant == 'terminal' else os.pipe()
                process = self.launch(script, WAITING, stderr=writer)
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
                child = int(process.stdout.readline())
                os.killpg(process.pid, signum)
                self.assertEqual(128 + signum, process.wait(180))
                process.stdout.close()
                self.assertEqual([], list(fixtures.iterdir()), 'a cluster directory was left behind')
                self.assertFalse(Path(sockets).exists(), 'the temporary socket directory was left behind')
                self.assertEqual([], processes_naming(root))
                self.assertFalse(Path('/proc/%d' % child).exists(), 'the command was left running')

    def test_a_signal_during_initdb_lets_it_finish_and_leaves_nothing_behind(self):
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
                # initdb was left to finish, so the cleanup never met its single-user backend.
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
        output, errors = finish(self.launch(launcher.__file__, PROBE, env=hostile))
        self.assertIn('Throwaway PostgreSQL stopped and exited: yes', errors)
        seen = json.loads(output)
        self.assertEqual([], seen['libpq'])
        self.assertTrue(seen['dsn'].startswith('host=/'), seen['dsn'])
        self.assertEqual([0o700, server_uid()], seen['socket_dir'])
        if RUN_AS:
            server = server_uid()
            self.assertEqual({'cluster': 0, 'metadata': 0, 'data': server, 'config': server, 'log': server}, seen['owners'])

    def test_a_deep_checkout_gets_a_private_socket_directory_under_tmp_whatever_tmpdir_says(self):
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
