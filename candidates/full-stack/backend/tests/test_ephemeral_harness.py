"""Local synthetic checks for a no-listener cloud harness; no cloud access."""
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from railway_candidate import ephemeral_harness as harness
from railway_candidate.ephemeral_supervisor import run


class EphemeralHarnessTests(unittest.TestCase):
    def test_real_in_process_synthetic_api_and_restart(self):
        root = (Path(__file__).resolve().parents[2] / 'build' /
                'ephemeral-harness-fixtures' / str(uuid4())).resolve()
        root.mkdir(parents=True)
        self.assertEqual(harness.run_checks(root), 22)

    def test_fixture_rejects_preexisting_database(self):
        root = (Path(__file__).resolve().parents[2] / 'build' /
                'ephemeral-harness-fixtures' / str(uuid4())).resolve()
        root.mkdir(parents=True)
        db = root / 'mom-health-synthetic.sqlite3'
        db.write_bytes(b'fictional-existing-fixture')
        with self.assertRaises(ValueError):
            harness.run_checks(root)
        self.assertEqual(db.read_bytes(), b'fictional-existing-fixture')

    def test_supervisor_strips_credentials_and_is_bounded(self):
        calls = []
        def fake(args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(returncode=0)
        env = {'MOM_HEALTH_SELFTEST':'1', 'MOM_HEALTH_ALLOW_REAL_DATA':'false',
               'MOM_HEALTH_VOLUME_MOUNT':'/data', 'MOM_HEALTH_UPLOAD_TOKEN':'secret'}
        self.assertEqual(run(env, fake), 'PASS')
        self.assertNotIn('MOM_HEALTH_UPLOAD_TOKEN', calls[0]['env'])
        self.assertEqual(calls[0]['timeout'], 25)
        self.assertEqual(calls[0]['stdout'], subprocess.DEVNULL)
        self.assertEqual(run({**env, 'MOM_HEALTH_ALLOW_REAL_DATA':'true'}, fake), 'MODE')
        self.assertEqual(len(calls), 1)

    def test_supervisor_timeout_and_unknown_results_are_safe(self):
        env = {'MOM_HEALTH_SELFTEST':'1', 'MOM_HEALTH_ALLOW_REAL_DATA':'false',
               'MOM_HEALTH_VOLUME_MOUNT':'/data'}
        def timed_out(*args, **kwargs): raise subprocess.TimeoutExpired('ignored', 25)
        self.assertEqual(run(env, timed_out), 'TIMEOUT')
        self.assertEqual(run(env, lambda *a, **k: SimpleNamespace(returncode=99)), 'UNKNOWN')

    def test_shared_or_overlapping_mounts_rejected(self):
        tmp, data = Path('/tmp'), Path('/data')
        with patch.object(harness.os, 'name', 'posix'), patch.object(Path, 'resolve', lambda p, **k:p), \
             patch.object(Path, 'is_dir', return_value=True), patch.object(harness.os.path, 'ismount', return_value=True), \
             patch.object(harness.os, 'stat', return_value=SimpleNamespace(st_dev=1)):
            with self.assertRaisesRegex(ValueError, 'shared filesystem'):
                harness.validate_tmp(tmp, data)
            with self.assertRaisesRegex(ValueError, 'overlapping paths'):
                harness.validate_tmp(data / 'tmp', data)


if __name__ == '__main__': unittest.main()
