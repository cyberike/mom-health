"""Noncreating storage checks; POSIX behavior is modeled on Windows."""
import errno
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from railway_candidate.existing_only_storage import _require_no_file_acl, validate_existing_storage


class FakePOSIX:
    name = 'posix'
    O_RDONLY, O_DIRECTORY, O_NOFOLLOW, O_NONBLOCK, O_CREAT = 0, 2, 4, 8, 16

    def __init__(self, *, file_mode=stat.S_IFREG | 0o600, replaced=False,
                 missing_child=False, missing_file=False):
        self.path = SimpleNamespace(ismount=lambda path: True)
        self.file_mode, self.replaced = file_mode, replaced
        self.missing_child, self.missing_file = missing_child, missing_file
        self.calls, self.closed = [], []

    def geteuid(self): return 42

    def open(self, path, flags, *, dir_fd=None):
        self.calls.append((path, flags, dir_fd))
        if dir_fd is None: return 31
        if path == 'mom-health-private':
            if self.missing_child: raise FileNotFoundError()
            return 33
        self.asserted = dir_fd in (31, 33) and path == 'mom-health-synthetic.sqlite3'
        return 32

    def stat(self, path, *, dir_fd, follow_symlinks):
        assert path == 'mom-health-synthetic.sqlite3' and dir_fd in (31, 33) and not follow_symlinks
        if self.missing_file: raise FileNotFoundError()
        return SimpleNamespace(st_mode=self.file_mode, st_nlink=1, st_uid=42, st_dev=7, st_ino=10)

    def fstat(self, fd):
        if fd in (31, 33):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=42)
        return SimpleNamespace(st_mode=self.file_mode, st_nlink=1, st_uid=42,
                               st_dev=7, st_ino=11 if self.replaced else 10)

    def close(self, fd): self.closed.append(fd)


class ExistingOnlyStorageChecks(unittest.TestCase):
    def setUp(self):
        self.root = (Path(__file__).resolve().parents[2] / 'build' /
                     'existing-only-fixtures' / str(uuid4())).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        if os.name == 'posix': os.chmod(self.root, 0o700)
        self.database = self.root / 'mom-health-synthetic.sqlite3'

    def test_missing_file_is_not_created(self):
        with patch('railway_candidate.existing_only_storage.os.path.ismount', return_value=True):
            with self.assertRaises(ValueError):
                validate_existing_storage(str(self.root), str(self.database))
        self.assertFalse(self.database.exists())

    def test_existing_file_is_not_modified(self):
        self.database.write_bytes(b'fictional fixture')
        if os.name == 'posix': os.chmod(self.database, 0o600)
        with patch('railway_candidate.existing_only_storage.os.path.ismount', return_value=True):
            self.assertEqual(str(self.database), validate_existing_storage(str(self.root), str(self.database)))
        self.assertEqual(b'fictional fixture', self.database.read_bytes())

    def test_posix_flags_are_noncreating_and_nonblocking(self):
        fake = FakePOSIX()
        with patch('railway_candidate.existing_only_storage.os', fake), \
             patch('railway_candidate.existing_only_storage._require_no_posix_acl'), \
             patch('railway_candidate.existing_only_storage._require_no_file_acl'):
            self.assertEqual(str(self.database), validate_existing_storage(str(self.root), str(self.database)))
        self.assertEqual(2, len(fake.calls))
        self.assertTrue(fake.asserted)
        self.assertTrue(all(not flags & fake.O_CREAT for _, flags, _ in fake.calls))
        self.assertTrue(fake.calls[-1][1] & fake.O_NONBLOCK)
        self.assertTrue(fake.calls[-1][1] & fake.O_NOFOLLOW)
        self.assertEqual([32, 31], fake.closed)

    def test_posix_fifo_rejected_before_open_and_replacement_rejected(self):
        for fake in (FakePOSIX(file_mode=stat.S_IFIFO | 0o600), FakePOSIX(replaced=True)):
            with self.subTest(mode=fake.file_mode, replaced=fake.replaced), \
                 patch('railway_candidate.existing_only_storage.os', fake), \
                 patch('railway_candidate.existing_only_storage._require_no_posix_acl'), \
                 patch('railway_candidate.existing_only_storage._require_no_file_acl'):
                with self.assertRaises(ValueError):
                    validate_existing_storage(str(self.root), str(self.database))
            self.assertEqual(2 if fake.replaced else 1, len(fake.calls))
            self.assertEqual([32, 31] if fake.replaced else [31], fake.closed)

    def test_missing_posix_child_or_file_fails_without_creation(self):
        for fake, private in ((FakePOSIX(missing_child=True), True),
                              (FakePOSIX(missing_file=True), False)):
            database = self.root / 'mom-health-private' / self.database.name if private else self.database
            with self.subTest(private=private), patch('railway_candidate.existing_only_storage.os', fake), \
                 patch('railway_candidate.existing_only_storage._require_no_posix_acl'), \
                 patch('railway_candidate.existing_only_storage._require_no_file_acl'):
                with self.assertRaises(ValueError):
                    validate_existing_storage(str(self.root), str(database), private_child=private)
            self.assertTrue(all(not flags & fake.O_CREAT for _, flags, _ in fake.calls))
            self.assertEqual([31], fake.closed)
            self.assertFalse(database.exists())

    def test_acl_failure_closes_all_open_descriptors(self):
        cases = ((False, [ValueError('root ACL')], None, [31]),
                 (True, [None, ValueError('child ACL')], None, [33, 31]),
                 (False, [None], ValueError('file ACL'), [32, 31]))
        for private, directory_results, file_error, expected_closed in cases:
            fake = FakePOSIX()
            database = self.root / 'mom-health-private' / self.database.name if private else self.database
            with self.subTest(private=private, expected=expected_closed), \
                 patch('railway_candidate.existing_only_storage.os', fake), \
                 patch('railway_candidate.existing_only_storage._require_no_posix_acl', side_effect=directory_results), \
                 patch('railway_candidate.existing_only_storage._require_no_file_acl', side_effect=file_error):
                with self.assertRaises(ValueError):
                    validate_existing_storage(str(self.root), str(database), private_child=private)
            self.assertEqual(expected_closed, fake.closed)
            self.assertTrue(all(not flags & fake.O_CREAT for _, flags, _ in fake.calls))

    def test_file_acl_only_absent_status_is_accepted(self):
        with patch('railway_candidate.existing_only_storage.os.getxattr', create=True,
                   side_effect=OSError(errno.ENODATA, 'absent')):
            _require_no_file_acl(42)
        for result in (b'present', OSError(errno.EPERM, 'denied'),
                       OSError(errno.EOPNOTSUPP, 'unsupported')):
            with self.subTest(result=type(result).__name__), \
                 patch('railway_candidate.existing_only_storage.os.getxattr', create=True) as getxattr:
                if isinstance(result, Exception): getxattr.side_effect = result
                else: getxattr.return_value = result
                with self.assertRaises(ValueError): _require_no_file_acl(42)

    @unittest.skipUnless(os.name == 'nt', 'Windows UNC path syntax')
    def test_literal_unc_is_rejected_before_resolving(self):
        root = r'\\server\share'
        database = root + r'\mom-health-synthetic.sqlite3'
        with patch.object(Path, 'resolve', side_effect=AssertionError('must not resolve')):
            with self.assertRaises(ValueError): validate_existing_storage(root, database)


if __name__ == '__main__': unittest.main()
