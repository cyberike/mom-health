"""Model tests for read-only synthetic preflight; not Linux or cloud evidence."""
import errno
import json
import stat
import subprocess
from types import SimpleNamespace
import unittest

from railway_candidate import private_preflight as probe
from railway_candidate import private_preflight_supervisor as supervisor


class Entry:
    def __init__(self, mode=None): self.mode = mode
    def lstat(self):
        if self.mode is None: raise FileNotFoundError()
        return SimpleNamespace(st_mode=self.mode)


class Root:
    def __init__(self, mode=0o40755, uid=123, child=None, db=None):
        self.mode, self.uid, self.child, self.db = mode, uid, child, db
    def lstat(self): return SimpleNamespace(st_mode=self.mode, st_uid=self.uid)
    def __truediv__(self, name):
        return Entry({'mom-health-private': self.child, 'mom-health-synthetic.sqlite3': self.db}[name])
    def __str__(self): return '/data'


class PrivatePreflightChecks(unittest.TestCase):
    def setUp(self):
        self.info = SimpleNamespace(read_text=lambda **kwargs: '10 1 0:1 / /data rw - ext4 /dev/x rw\n')
        self.env = {'MOM_HEALTH_SELFTEST':'1', 'MOM_HEALTH_ALLOW_REAL_DATA':'false',
                    'MOM_HEALTH_VOLUME_MOUNT':'/data', 'MOM_HEALTH_UPLOAD_TOKEN':'secret-value'}
    @staticmethod
    def no_acl(*args, **kwargs): raise OSError(errno.ENODATA, 'sensitive')
    def inspect(self, root, getter=None):
        return probe.inspect(root, self.info, getxattr=getter or self.no_acl,
                             geteuid=lambda:123, ismount=lambda root:True)

    def test_safe_metadata_and_unsafe_files_are_classified(self):
        self.assertEqual(self.inspect(Root()), supervisor.SAFE)
        for mode, label in ((stat.S_IFDIR | 0o700, 'DIRECTORY'),
                            (stat.S_IFREG | 0o600, 'REGULAR'),
                            (stat.S_IFLNK | 0o777, 'SYMLINK')):
            with self.subTest(mode=mode):
                result = self.inspect(Root(child=mode, db=mode))
                self.assertEqual(result['child'], label)
                self.assertEqual(result['legacy_db'], label)
        self.assertEqual(self.inspect(Root(mode=0o40777))['other_writable'], 'YES')
        self.assertEqual(self.inspect(Root(uid=124))['owner'], 'NO')

    def test_acl_present_and_unknown_do_not_pass(self):
        self.assertEqual(self.inspect(Root(), lambda *a, **k: b'private')['access_acl'], 'PRESENT')
        def unsupported(*a, **k): raise OSError(errno.ENOTSUP, 'sensitive')
        result = self.inspect(Root(), unsupported)
        self.assertEqual(result['access_acl'], 'UNKNOWN')
        self.assertEqual(result['default_acl'], 'UNKNOWN')
        self.assertNotEqual(result, supervisor.SAFE)

    def test_supervisor_validates_output_and_strips_credentials(self):
        def fake(*args, **kwargs):
            self.assertEqual(kwargs['timeout'], 25)
            self.assertNotIn('MOM_HEALTH_UPLOAD_TOKEN', kwargs['env'])
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            return SimpleNamespace(returncode=0, stdout=json.dumps(supervisor.SAFE).encode())
        self.assertTrue(supervisor.run(self.env, fake).startswith('PREFLIGHT PASS:'))
        bad = dict(supervisor.SAFE, child='DIRECTORY')
        self.assertTrue(supervisor.run(self.env, lambda *a, **k: SimpleNamespace(returncode=0,
            stdout=json.dumps(bad).encode())).startswith('PREFLIGHT STOP:'))
        bad['child'] = 'secret-value'
        result = supervisor.run(self.env, lambda *a, **k: SimpleNamespace(returncode=0,
            stdout=json.dumps(bad).encode()))
        self.assertEqual(result, 'PREFLIGHT FAILED: METADATA_UNAVAILABLE')
        self.assertNotIn('secret-value', result)

    def test_rejected_configuration_timeout_and_invalid_output(self):
        def forbidden(*a, **k): self.fail('child must not launch')
        self.assertEqual(supervisor.run({}, forbidden), 'PREFLIGHT REJECTED: CONFIGURATION')
        self.assertEqual(supervisor.run(dict(self.env, MOM_HEALTH_ALLOW_REAL_DATA='true'), forbidden),
                         'PREFLIGHT REJECTED: CONFIGURATION')
        def timeout(*a, **k): raise subprocess.TimeoutExpired('sensitive', 25)
        self.assertEqual(supervisor.run(self.env, timeout), 'PREFLIGHT FAILED: TIMEOUT')
        for output in (b'not-json', b'{}', b'x' * 4097):
            result = supervisor.run(self.env, lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
            self.assertEqual(result, 'PREFLIGHT FAILED: METADATA_UNAVAILABLE')


if __name__ == '__main__': unittest.main()
