"""Disposable fixtures only. Tests do not start a listener or contact a device."""
import os
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from railway_candidate.selftest import main, run_selftest, DiagnosticFailure, EXIT_CODES, _initialization_category


class SelfTestChecks(unittest.TestCase):
    def setUp(self):
        self.folder = (Path(__file__).resolve().parents[2] / 'build' / 'selftest-fixtures' / str(uuid4())).resolve()
        self.folder.mkdir(parents=True)
        if os.name == 'posix':
            self.folder.chmod(0o700)
        self.db = self.folder / 'mom-health-synthetic.sqlite3'

    def test_end_to_end_synthetic_and_no_overwrite(self):
        # Windows test has no private mount: do not claim actual mount verification.
        with patch('railway_candidate.gateway.os.path.ismount', return_value=True):
            self.assertEqual(run_selftest(str(self.folder), str(self.db)), 12)
            contents = self.db.read_bytes()
            with self.assertRaises(ValueError):
                run_selftest(str(self.folder), str(self.db))
            self.assertEqual(self.db.read_bytes(), contents)

    def test_unmounted_path_fails_closed(self):
        with patch('railway_candidate.gateway.os.path.ismount', return_value=False):
            with self.assertRaises(ValueError):
                run_selftest(str(self.folder), str(self.db))
        self.assertFalse(self.db.exists())

    def test_explicit_synthetic_only_required(self):
        with patch.dict(os.environ, {'MOM_HEALTH_SELFTEST': '1', 'MOM_HEALTH_ALLOW_REAL_DATA': 'true'}):
            self.assertEqual(main(), EXIT_CODES['MODE_REJECTED'])
        with patch.dict(os.environ, {'MOM_HEALTH_SELFTEST': '0', 'MOM_HEALTH_ALLOW_REAL_DATA': 'false'}):
            self.assertEqual(main(), EXIT_CODES['MODE_REJECTED'])
        self.assertFalse(self.db.exists())

    def test_fixed_storage_diagnostics_and_no_sensitive_exception_output(self):
        from io import StringIO
        from contextlib import redirect_stdout
        self.assertEqual(_initialization_category(ValueError('Configured storage must be an existing non-symlink mount')), 'STORAGE_MOUNT_INVALID')
        self.assertEqual(_initialization_category(ValueError('Storage directory must be owned by the service user and private (0700)')), 'STORAGE_PERMISSIONS')
        self.assertEqual(_initialization_category(ValueError('Synthetic storage unavailable or unsafe')), 'STORAGE_UNAVAILABLE')
        self.assertEqual(_initialization_category(ValueError('secret/path/token')), 'GATEWAY_INIT_FAILED')
        with patch.dict(os.environ, {'MOM_HEALTH_SELFTEST': '1', 'MOM_HEALTH_ALLOW_REAL_DATA': 'false'}), \
             patch('railway_candidate.selftest.run_selftest', side_effect=ValueError('secret/path/token')), \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(main(), EXIT_CODES['EXECUTION_FAILED'])
            self.assertEqual(output.getvalue().strip(), 'SELFTEST FAIL: EXECUTION_FAILED')

    def test_distinguishes_existing_database_without_writing_it(self):
        self.db.write_bytes(b'previous-fixture')
        with self.assertRaises(DiagnosticFailure) as error:
            run_selftest(str(self.folder), str(self.db))
        self.assertEqual(error.exception.category, 'DB_EXISTS')
        self.assertEqual(self.db.read_bytes(), b'previous-fixture')

    def test_storage_failure_flows_to_fixed_exit_code_without_path(self):
        from io import StringIO
        from contextlib import redirect_stdout
        env = {'MOM_HEALTH_SELFTEST': '1', 'MOM_HEALTH_ALLOW_REAL_DATA': 'false',
               'MOM_HEALTH_VOLUME_MOUNT': str(self.folder), 'MOM_HEALTH_SYNTHETIC_DB_PATH': str(self.db)}
        error = ValueError('Configured storage must be an existing non-symlink mount')
        with patch.dict(os.environ, env), patch('railway_candidate.gateway.create_gateway', side_effect=error), \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(main(), EXIT_CODES['STORAGE_MOUNT_INVALID'])
            self.assertEqual(output.getvalue().strip(), 'SELFTEST FAIL: STORAGE_MOUNT_INVALID')
            self.assertNotIn(str(self.folder), output.getvalue())

    def test_simultaneous_starters_do_not_reuse_database(self):
        # Both threads must pass selftest's preliminary check before either
        # reaches the atomic open, exercising the previous race deterministically.
        from railway_candidate.gateway import create_gateway
        gate = Barrier(2)
        def synchronized_gateway(*args, **kwargs):
            gate.wait(timeout=10)
            return create_gateway(*args, **kwargs)
        def start():
            try:
                return run_selftest(str(self.folder), str(self.db))
            except DiagnosticFailure as error:
                return error.category
        with patch('railway_candidate.gateway.os.path.ismount', return_value=True), \
             patch('railway_candidate.gateway.create_gateway', side_effect=synchronized_gateway), \
             ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: start(), range(2)))
        self.assertEqual(results.count(12), 1)
        self.assertEqual(results.count('DB_EXISTS'), 1)
        self.assertGreater(self.db.stat().st_size, 0)


if __name__ == '__main__':
    unittest.main()
