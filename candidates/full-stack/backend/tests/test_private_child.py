"""Synthetic model checks for the opt-in POSIX private-child storage path.

Windows runs use a mocked POSIX API; they cannot establish real Linux ACL,
mount, race or file-descriptor behavior. No cloud or health data is used.
"""
import os
import errno
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from railway_candidate.gateway import validate_private_child_storage
from railway_candidate.selftest_supervisor import run_supervisor
from railway_candidate.selftest import _initialization_category


class FakePOSIX:
    name = 'posix'
    O_RDONLY, O_WRONLY, O_DIRECTORY, O_NOFOLLOW, O_CREAT, O_EXCL = 1, 2, 4, 8, 16, 32
    def __init__(self, *, root_mode=0o755, child_mode=0o700, root_uid=123, child_uid=123,
                 existing_db=False, bad_child=False, child_exists=False, db_info=None, acls=None, child_acls=None):
        self.path = SimpleNamespace(ismount=lambda path: True)
        self.root_mode, self.child_mode = root_mode, child_mode
        self.root_uid, self.child_uid = root_uid, child_uid
        self.existing_db, self.bad_child, self.child_exists = existing_db, bad_child, child_exists
        self.db_info = db_info or SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=123)
        self.acls = acls or {}
        self.child_acls = child_acls or {}
        self.acl_checks = []
        self.db_accessed = False
        self.created_dir = self.created_file = False
        self.closed = []
        self.root_fd = 31
        self.child_fd = 32
        self.db_fd = 33
    def geteuid(self): return 123
    def getxattr(self, fd, name):
        assert fd in (self.root_fd, self.child_fd)
        self.acl_checks.append((fd, name))
        outcome = (self.acls if fd == self.root_fd else self.child_acls).get(name, errno.ENODATA)
        if isinstance(outcome, int): raise OSError(outcome, 'synthetic ACL error')
        return outcome
    def fstat(self, fd):
        return SimpleNamespace(st_uid=self.root_uid if fd == self.root_fd else self.child_uid,
                               st_mode=stat.S_IFDIR | (self.root_mode if fd == self.root_fd else self.child_mode))
    def mkdir(self, name, mode, *, dir_fd):
        assert name == 'mom-health-private' and mode == 0o700 and dir_fd == self.root_fd
        if self.child_exists: raise FileExistsError()
        self.created_dir = True
    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            assert flags & self.O_DIRECTORY and flags & self.O_NOFOLLOW
            return self.root_fd
        if dir_fd == self.root_fd:
            assert path == 'mom-health-private' and flags & self.O_NOFOLLOW
            if self.bad_child: raise OSError('non-directory or symlink')
            return self.child_fd
        assert dir_fd == self.child_fd and path == 'mom-health-synthetic.sqlite3'
        self.db_accessed = True
        assert flags & self.O_EXCL and flags & self.O_NOFOLLOW and mode == 0o600
        if self.existing_db: raise FileExistsError()
        self.created_file = True
        return self.db_fd
    def close(self, fd): self.closed.append(fd)
    def stat(self, path, *, dir_fd, follow_symlinks):
        assert path == 'mom-health-synthetic.sqlite3' and dir_fd == self.child_fd and not follow_symlinks
        return self.db_info


class PrivateChildChecks(unittest.TestCase):
    def setUp(self):
        self.root = (Path(__file__).resolve().parents[2]/'build'/'private-child-fixtures'/str(uuid4())).resolve()
        self.root.mkdir(parents=True)
        self.db = self.root/'mom-health-private'/'mom-health-synthetic.sqlite3'
    def call(self, fake, **kwargs):
        with patch('railway_candidate.gateway.os', fake):
            return validate_private_child_storage(str(self.root), str(self.db), **kwargs)
    def test_private_child_created_without_chmod_parent(self):
        fake = FakePOSIX()
        self.assertEqual(self.call(fake, require_new=True), str(self.db))
        self.assertTrue(fake.created_dir and fake.created_file)
        self.assertEqual(fake.acl_checks, [(fd, name) for fd in (fake.root_fd, fake.child_fd)
                         for name in ('system.posix_acl_access', 'system.posix_acl_default')])
        self.assertFalse(self.db.exists())  # mocked API did not modify the fixture
    def test_untrusted_volume_rejected_before_child_creation(self):
        for fake in (FakePOSIX(root_mode=0o777), FakePOSIX(root_mode=0o775), FakePOSIX(root_uid=124)):
            with self.subTest(mode=fake.root_mode, uid=fake.root_uid), self.assertRaises(ValueError):
                self.call(fake, require_new=True)
            self.assertFalse(fake.created_dir or fake.created_file)
    def test_present_or_unreadable_acls_reject_before_writes(self):
        names = ('system.posix_acl_access', 'system.posix_acl_default')
        for name in names:
            for outcome, category in ((b'acl', 'STORAGE_ACL_PRESENT'),
                                      (errno.EOPNOTSUPP, 'STORAGE_ACL_UNAVAILABLE'),
                                      (errno.EPERM, 'STORAGE_ACL_UNAVAILABLE')):
                fake = FakePOSIX(acls={name: outcome})
                with self.subTest(name=name, outcome=outcome):
                    with self.assertRaises(ValueError) as error:
                        self.call(fake, require_new=True)
                    self.assertEqual(_initialization_category(error.exception), category)
                    self.assertFalse(fake.created_dir or fake.created_file)
                    self.assertEqual(fake.closed, [fake.root_fd])
    def test_existing_unsafe_child_and_symlink_rejected(self):
        for fake in (FakePOSIX(child_mode=0o755, child_exists=True), FakePOSIX(child_uid=124, child_exists=True), FakePOSIX(bad_child=True, child_exists=True)):
            with self.assertRaises(ValueError): self.call(fake)
            self.assertFalse(fake.created_file)
    def test_existing_child_acls_fail_closed_before_database_access(self):
        names = ('system.posix_acl_access', 'system.posix_acl_default')
        for name in names:
            for outcome, category in ((b'acl', 'STORAGE_ACL_PRESENT'),
                                      (errno.EOPNOTSUPP, 'STORAGE_ACL_UNAVAILABLE'),
                                      (errno.EPERM, 'STORAGE_ACL_UNAVAILABLE')):
                fake = FakePOSIX(child_exists=True, existing_db=True, child_acls={name: outcome})
                with self.subTest(name=name, outcome=outcome):
                    with self.assertRaises(ValueError) as error:
                        self.call(fake)
                    self.assertEqual(_initialization_category(error.exception), category)
                    self.assertFalse(fake.created_dir or fake.created_file or fake.db_accessed)
                    self.assertEqual(fake.closed, [fake.child_fd, fake.root_fd])
    def test_existing_database_not_overwritten(self):
        # Simulate a database appearing after fresh child creation.
        fake = FakePOSIX(existing_db=True)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.call(fake, require_new=True)
        self.assertTrue(fake.created_dir)
        self.assertFalse(fake.created_file)
        self.assertEqual(fake.closed, [fake.child_fd, fake.root_fd])

    def test_fresh_test_rejects_existing_child_before_open_or_write(self):
        fake = FakePOSIX(child_exists=True)
        with self.assertRaisesRegex(ValueError, 'private child already exists') as error:
            self.call(fake, require_new=True)
        self.assertEqual(_initialization_category(error.exception), 'CHILD_EXISTS')
        self.assertFalse(fake.created_dir or fake.created_file)
        self.assertEqual(fake.closed, [fake.root_fd])

    def test_child_open_failure_closes_root_and_preserves_database(self):
        fake = FakePOSIX(bad_child=True, child_exists=True)
        with self.assertRaisesRegex(ValueError, 'unavailable or unsafe'):
            self.call(fake)
        self.assertEqual(fake.closed, [fake.root_fd])
        self.assertFalse(fake.created_file)

    def test_unsafe_root_and_child_have_fixed_safe_diagnostic(self):
        for fake in (FakePOSIX(root_mode=0o777), FakePOSIX(child_mode=0o755, child_exists=True)):
            with self.subTest(mode=(fake.root_mode, fake.child_mode)):
                try:
                    self.call(fake)
                except ValueError as exc:
                    self.assertEqual(_initialization_category(exc), 'STORAGE_PERMISSIONS')
                else:
                    self.fail('Unsafe permissions must be rejected')
                self.assertFalse(fake.created_file)

    def test_existing_database_reuse_requires_private_owned_regular_single_link(self):
        cases = ((stat.S_IFREG | 0o600, 1, 123, True),
                 (stat.S_IFREG | 0o640, 1, 123, False),
                 (stat.S_IFREG | 0o600, 2, 123, False),
                 (stat.S_IFREG | 0o600, 1, 124, False),
                 (stat.S_IFLNK | 0o600, 1, 123, False))
        for mode, links, uid, valid in cases:
            fake = FakePOSIX(existing_db=True, child_exists=True,
                db_info=SimpleNamespace(st_mode=mode, st_nlink=links, st_uid=uid))
            with self.subTest(mode=mode, links=links, uid=uid):
                if valid:
                    self.assertEqual(self.call(fake), str(self.db))
                else:
                    with self.assertRaises(ValueError): self.call(fake)
                self.assertFalse(fake.created_dir or fake.created_file)
                self.assertEqual(fake.closed, [fake.child_fd, fake.root_fd])
    def test_paths_and_unsupported_platform_fail_closed(self):
        with self.assertRaises(ValueError): validate_private_child_storage(str(self.root), str(self.root/'wrong.sqlite3'))
        if os.name != 'posix':
            with self.assertRaisesRegex(ValueError, 'POSIX'): validate_private_child_storage(str(self.root), str(self.db))
        with patch('railway_candidate.gateway.os.path.ismount', return_value=False), self.assertRaises(ValueError):
            validate_private_child_storage(str(self.root), str(self.db))
    def test_supervisor_opt_in_checks_exact_path_and_does_not_inherit_secret(self):
        calls = []
        def runner(args, **kwargs): calls.append(kwargs); return SimpleNamespace(returncode=0)
        base = {'MOM_HEALTH_SELFTEST':'1', 'MOM_HEALTH_ALLOW_REAL_DATA':'false',
                'MOM_HEALTH_VOLUME_MOUNT':str(self.root), 'MOM_HEALTH_SYNTHETIC_DB_PATH':str(self.db),
                'MOM_HEALTH_PRIVATE_CHILD':'true', 'MOM_HEALTH_UPLOAD_TOKEN':'secret'}
        self.assertEqual(run_supervisor(base, runner), 'PASS')
        self.assertEqual(calls[0]['env']['MOM_HEALTH_PRIVATE_CHILD'], 'true')
        self.assertNotIn('MOM_HEALTH_UPLOAD_TOKEN', calls[0]['env'])
        self.assertEqual(run_supervisor({**base, 'MOM_HEALTH_PRIVATE_CHILD':'false'}, runner), 'PATH_INVALID')
        self.assertEqual(run_supervisor({**base, 'MOM_HEALTH_PRIVATE_CHILD':'unknown'}, runner), 'MODE_REJECTED')
        self.assertEqual(run_supervisor(base, lambda *a, **kw: SimpleNamespace(returncode=21)), 'CHILD_EXISTS')
        self.assertEqual(run_supervisor(base, lambda *a, **kw: SimpleNamespace(returncode=22)), 'STORAGE_ACL_UNAVAILABLE')
        self.assertEqual(run_supervisor(base, lambda *a, **kw: SimpleNamespace(returncode=23)), 'STORAGE_ACL_PRESENT')


if __name__ == '__main__': unittest.main()
