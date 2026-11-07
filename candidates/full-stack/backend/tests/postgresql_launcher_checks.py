"""Opt-in checks of the throwaway PostgreSQL launcher itself. Fictional data only.

Not collected by ``unittest discover``. Needs Linux and the PostgreSQL server
binaries; as root, the launcher's server runs as the postgres account. Each
check runs the launcher in a subprocess with a small probe command and the
--discard option, so every cluster it creates is removed again. From
candidates/full-stack/backend:

    python -m unittest tests.postgresql_launcher_checks -v
"""
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import postgresql_throwaway as launcher  # noqa: E402

RUN_AS = ['--run-as', 'postgres'] if os.geteuid() == 0 else []
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
                  'owners': {'cluster': owner(cluster), 'metadata': owner(cluster / 'cluster.json'), 'data': owner(info['data']),
                             'config': owner(Path(info['data']) / 'postgresql.conf'), 'log': owner(Path(info['log']).parent)}}))
'''


def binaries_available():
    try:
        return (launcher.find_bindir() / 'initdb').exists()
    except SystemExit:
        return False


def processes_naming(path):
    return [entry.name for entry in Path('/proc').glob('[0-9]*') if (entry / 'cmdline').exists() and str(path).encode() in _cmdline(entry)]


def _cmdline(entry):
    try:
        return (entry / 'cmdline').read_bytes()
    except OSError:
        return b''


class VersionChoiceTests(unittest.TestCase):
    def test_the_highest_installed_version_number_is_chosen(self):
        root = Path(tempfile.mkdtemp(prefix='versions-', dir=launcher.FIXTURES.parent))
        self.addCleanup(shutil.rmtree, root)
        for version in ('9.6', '16', '10'):
            (root / version / 'bin').mkdir(parents=True)
            (root / version / 'bin' / 'pg_ctl').touch()
        with patch('shutil.which', return_value=None):
            self.assertEqual(root / '16' / 'bin', launcher.find_bindir(root=root))


@unittest.skipUnless(sys.platform.startswith('linux') and binaries_available(), 'needs Linux and the PostgreSQL server binaries')
class LauncherChecks(unittest.TestCase):
    def launch(self, command, *options, script=launcher.__file__, env=None):
        return subprocess.Popen([sys.executable, script, *RUN_AS, '--discard', *options, '--', sys.executable, '-c', command],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)

    def test_a_terminated_or_hung_up_launcher_stops_the_server_and_discards_the_cluster(self):
        for signum, whole_group in ((signal.SIGTERM, True), (signal.SIGHUP, False)):
            with self.subTest(signal=signum.name):
                process = self.launch(WAITING)
                try:
                    cluster = None
                    for line in process.stderr:
                        if line.startswith('Throwaway PostgreSQL ('):
                            cluster = Path(line.rsplit(': ', 1)[1].strip())
                            break
                    self.assertIsNotNone(cluster, 'the launcher never reported its cluster')
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

    def test_a_failed_setup_leaves_no_cluster_directory_behind(self):
        before = set(launcher.FIXTURES.iterdir()) if launcher.FIXTURES.exists() else set()
        process = self.launch('pass', '--bindir', '/nonexistent-postgresql-bindir')
        process.communicate(timeout=120)
        self.assertNotEqual(0, process.returncode)
        self.assertEqual(before, set(launcher.FIXTURES.iterdir()) if launcher.FIXTURES.exists() else set())

    def test_libpq_variables_never_reach_a_client_and_only_the_server_account_owns_its_files(self):
        hostile = dict(os.environ, PGHOSTADDR='192.0.2.1', PGPORT='1', PGSERVICE='synthetic-missing-service')
        output, errors = self.launch(PROBE, env=hostile).communicate(timeout=180)
        self.assertIn('Throwaway PostgreSQL stopped and exited: yes', errors)
        seen = json.loads(output)
        self.assertEqual([], seen['libpq'])
        self.assertTrue(seen['dsn'].startswith('host=/'), seen['dsn'])
        if RUN_AS:
            server = pwd.getpwnam('postgres').pw_uid
            self.assertEqual({'cluster': 0, 'metadata': 0, 'data': server, 'config': server, 'log': server}, seen['owners'])

    def test_a_deep_checkout_gets_a_short_private_socket_directory(self):
        root = launcher.FIXTURES.parent / 'launcher-depth-check'
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        copy = root / ('deep-checkout-' + 'd' * 16) / 'backend' / 'tests' / 'postgresql_throwaway.py'
        copy.parent.mkdir(parents=True)
        shutil.copy(launcher.__file__, copy)
        output, errors = self.launch(PROBE, script=str(copy)).communicate(timeout=180)
        self.assertIn('Throwaway PostgreSQL stopped and exited: yes', errors)
        sockets = Path(json.loads(output)['sockets'])
        self.assertEqual(Path(tempfile.gettempdir()), sockets.parent)
        self.assertTrue(sockets.name.startswith('mhpg-'))
        self.assertFalse(sockets.exists(), 'the temporary socket directory was left behind')


if __name__ == '__main__':
    unittest.main()
