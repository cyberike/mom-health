"""No listener or Railway access; process launch is mocked in these unit tests."""
import os
from pathlib import Path
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
from uuid import uuid4
from types import SimpleNamespace
from railway_candidate.selftest_supervisor import run_supervisor, main, TIMEOUT_SECONDS


class SupervisorChecks(unittest.TestCase):
    def setUp(self):
        root = Path.cwd().resolve()
        self.env = {'MOM_HEALTH_SELFTEST': '1', 'MOM_HEALTH_ALLOW_REAL_DATA': 'false',
                    'MOM_HEALTH_VOLUME_MOUNT': str(root),
                    'MOM_HEALTH_SYNTHETIC_DB_PATH': str(root / 'mom-health-synthetic.sqlite3'),
                    'MOM_HEALTH_UPLOAD_TOKEN': 'must-not-reach-child', 'PATH': os.environ.get('PATH', '')}

    def test_success_timeout_and_no_credential_in_child(self):
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0)
        self.assertEqual(run_supervisor(self.env, runner), 'PASS')
        args, options = calls[0]
        self.assertEqual(args[1:], ['-m', 'railway_candidate.selftest'])
        self.assertEqual(options['timeout'], 25)
        self.assertEqual(options['timeout'], TIMEOUT_SECONDS)
        self.assertIs(options['stdout'], subprocess.DEVNULL)
        self.assertIs(options['stderr'], subprocess.DEVNULL)
        self.assertFalse(options['shell'])
        self.assertNotIn('MOM_HEALTH_UPLOAD_TOKEN', options['env'])
        self.assertNotIn('PYTHONPATH', options['env'])
        self.assertEqual(options['env']['PYTHONNOUSERSITE'], '1')
        def expired(*args, **kwargs):
            raise subprocess.TimeoutExpired('synthetic-child', TIMEOUT_SECONDS)
        self.assertEqual(run_supervisor(self.env, expired), 'CHILD_TIMEOUT')
        self.assertEqual(run_supervisor(self.env, lambda *a, **kw: SimpleNamespace(returncode=1)), 'CHILD_UNCLASSIFIED')
        self.assertEqual(run_supervisor(self.env, lambda *a, **kw: SimpleNamespace(returncode=13)), 'STORAGE_MOUNT_INVALID')
        self.assertEqual(run_supervisor(self.env, lambda *a, **kw: SimpleNamespace(returncode=14)), 'STORAGE_PERMISSIONS')
        self.assertEqual(run_supervisor(self.env, lambda *a, **kw: SimpleNamespace(returncode=20)), 'DEPENDENCY_IMPORT_FAILED')
        def failed(*args, **kwargs):
            raise OSError('sensitive path or command')
        self.assertEqual(run_supervisor(self.env, failed), 'CHILD_LAUNCH_FAILED')

    def test_disabled_real_data_and_bad_paths_do_not_launch(self):
        def forbidden(*args, **kwargs):
            self.fail('Unexpected child launch')
        for changes in ({'MOM_HEALTH_SELFTEST': '0'}, {'MOM_HEALTH_ALLOW_REAL_DATA': 'true'},
                        {'MOM_HEALTH_SYNTHETIC_DB_PATH': 'relative.sqlite3'},
                        {'MOM_HEALTH_SYNTHETIC_DB_PATH': str(Path(self.env['MOM_HEALTH_VOLUME_MOUNT'])/'wrong.sqlite3')}):
            self.assertIn(run_supervisor({**self.env, **changes}, forbidden), ('MODE_REJECTED', 'PATH_INVALID'))

    def test_real_child_reports_unmounted_fixture_without_disclosing_path_or_token(self):
        # A unique ordinary directory is not a mount. No listener or network request occurs.
        folder = (Path(__file__).resolve().parents[2] / 'build' / 'selftest-supervisor-fixtures' / str(uuid4())).resolve()
        folder.mkdir(parents=True)
        env = {**self.env, 'MOM_HEALTH_VOLUME_MOUNT': str(folder),
               'MOM_HEALTH_SYNTHETIC_DB_PATH': str(folder / 'mom-health-synthetic.sqlite3')}
        # System/venv dependencies may be available, or may live only in this
        # user's disabled Python site. Neither path starts an external service.
        self.assertIn(run_supervisor(env), ('STORAGE_MOUNT_INVALID', 'DEPENDENCY_IMPORT_FAILED'))
        self.assertFalse((folder / 'mom-health-synthetic.sqlite3').exists())

    def test_unexpected_exception_is_not_printed(self):
        with patch('railway_candidate.selftest_supervisor.run_supervisor', side_effect=RuntimeError('secret token and private path')), redirect_stdout(StringIO()) as output:
            self.assertEqual(main(), 1)
            self.assertEqual(output.getvalue().strip(), 'SUPERVISOR FAIL: SUPERVISOR_INTERNAL_ERROR; inspect privately before retry')


if __name__ == '__main__':
    unittest.main()
