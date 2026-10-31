"""Local metadata tests; no real volume or Railway contact."""
import os
from pathlib import Path, PurePosixPath
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

from railway_candidate import inspect_volume, inspect_supervisor

class InspectionChecks(unittest.TestCase):
    def test_mountinfo_exact_target_and_escapes(self):
        text = '10 1 0:1 / /data rw - ext4 /dev/x rw\n11 1 0:1 / /data\\040backup rw - ext4 /dev/x rw'
        self.assertTrue(inspect_volume.mountinfo_has_target(text, PurePosixPath('/data')))
        self.assertTrue(inspect_volume.mountinfo_has_target(text, PurePosixPath('/data backup')))
        self.assertFalse(inspect_volume.mountinfo_has_target(text, PurePosixPath('/dat')))

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX metadata')
    def test_read_only_metadata_and_unsafe_entry(self):
        root = Path(__file__).resolve().parents[2] / 'build' / 'inspection-fixtures' / str(uuid4())
        root.mkdir(parents=True, mode=0o700)
        mountinfo = root.parent / (root.name + '.mountinfo')
        mountinfo.write_text('10 1 0:1 / ' + str(root) + ' rw - ext4 /dev/x rw\n')
        before = sorted(p.name for p in root.iterdir())
        flags = inspect_volume.inspect(root, mountinfo)
        self.assertEqual(sorted(p.name for p in root.iterdir()), before)
        self.assertTrue(flags & 2)
        self.assertFalse(flags & 16)
        (root / inspect_volume.DB_NAME).symlink_to('absent')
        flags = inspect_volume.inspect(root, mountinfo)
        self.assertEqual(flags & 48, 48)

    def test_reject_mode_and_path_before_child(self):
        def forbidden(*a, **k): self.fail('child launched')
        self.assertIn('REJECTED', inspect_supervisor.run({}, forbidden))
        env = {'MOM_HEALTH_SELFTEST':'1','MOM_HEALTH_ALLOW_REAL_DATA':'true',
               'MOM_HEALTH_VOLUME_MOUNT':'/data','MOM_HEALTH_SYNTHETIC_DB_PATH':'/data/mom-health-synthetic.sqlite3'}
        self.assertIn('REJECTED', inspect_supervisor.run(env, forbidden))
        env['MOM_HEALTH_ALLOW_REAL_DATA']='false';env['MOM_HEALTH_VOLUME_MOUNT']='/other'
        self.assertIn('REJECTED', inspect_supervisor.run(env, forbidden))

    def test_metadata_and_import_flags_without_real_mount(self):
        class Database:
            def lstat(self): raise FileNotFoundError()
        class Root:
            def lstat(self): return SimpleNamespace(st_mode=0o40700, st_uid=42)
            def __truediv__(self, name):
                self_name = name
                if self_name != inspect_volume.DB_NAME: raise AssertionError('unexpected path')
                return Database()
        class Mountinfo:
            def read_text(self, encoding): return 'synthetic'
        with patch.object(inspect_volume.os, 'name', 'posix'), \
             patch.object(inspect_volume.os, 'geteuid', return_value=42, create=True), \
             patch.object(inspect_volume.os.path, 'ismount', return_value=True), \
             patch.object(inspect_volume, 'mountinfo_has_target', return_value=True), \
             patch.object(inspect_volume.importlib, 'import_module', return_value=object()):
            self.assertEqual(inspect_volume.inspect(Root(), Mountinfo()), 1+2+4+8+64)
            with patch.object(inspect_volume.importlib, 'import_module', side_effect=ImportError('private')):
                self.assertEqual(inspect_volume.inspect(Root(), Mountinfo()), 1+2+4+8)
    def test_timeout_and_fixed_labels_no_secret_in_child(self):
        env={'MOM_HEALTH_SELFTEST':'1','MOM_HEALTH_ALLOW_REAL_DATA':'false',
             'MOM_HEALTH_VOLUME_MOUNT':'/data','MOM_HEALTH_SYNTHETIC_DB_PATH':'/data/mom-health-synthetic.sqlite3',
             'MOM_HEALTH_UPLOAD_TOKEN':'not-for-child'}
        def fake(*args, **kwargs):
            self.assertNotIn('MOM_HEALTH_UPLOAD_TOKEN', kwargs['env'])
            self.assertIs(kwargs['stdout'], subprocess.DEVNULL)
            self.assertIs(kwargs['stderr'], subprocess.DEVNULL)
            self.assertEqual(kwargs['timeout'],25)
            return SimpleNamespace(returncode=128+1+2+4+8+16+64)
        result=inspect_supervisor.run(env,fake)
        self.assertEqual(result,'INSPECTION COMPLETE: ISMOUNT=YES MOUNTINFO=YES OWNER_MATCH=YES PRIVATE_MODE=YES DATABASE=REGULAR IMPORTS=OK')
        self.assertNotIn('not-for-child',result)
        def timeout(*a,**k): raise subprocess.TimeoutExpired('sensitive',25)
        self.assertEqual(inspect_supervisor.run(env,timeout),'INSPECTION FAILED: TIMEOUT')
        self.assertEqual(inspect_supervisor.run(env,lambda *a,**k:SimpleNamespace(returncode=1)), 'INSPECTION FAILED: METADATA_UNAVAILABLE')
        self.assertIn('IMPORTS=FAILED', inspect_supervisor.run(env,lambda *a,**k:SimpleNamespace(returncode=128)))

if __name__ == '__main__': unittest.main()
