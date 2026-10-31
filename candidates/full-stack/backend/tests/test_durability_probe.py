"""Synthetic local tests; Windows does not validate real POSIX metadata."""
import errno
from pathlib import Path
import sqlite3
import stat
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from railway_candidate import durability_probe as probe
from railway_candidate import durability_supervisor as supervisor


class DurabilityTests(unittest.TestCase):
    def setUp(self):
        # Unique synthetic fixtures stay inside the task workspace; never touch
        # the previously denied system temporary directories.
        self.fixture_dir = Path(__file__).resolve().parents[2] / 'build' / 'durability-fixtures' / str(uuid4())
        self.fixture_dir.mkdir(parents=True)
        self.path = self.fixture_dir / 'fixture.sqlite3'
        db = sqlite3.connect(self.path)
        db.executescript('CREATE TABLE samples (synthetic INTEGER NOT NULL);'
                         'CREATE TABLE batches (id INTEGER PRIMARY KEY);'
                         'CREATE TABLE gateway_status (synthetic INTEGER NOT NULL);'
                         'INSERT INTO samples VALUES (1); INSERT INTO batches VALUES (1);'
                         'INSERT INTO batches VALUES (2);')
        db.commit()
        db.close()

    def test_matching_synthetic_database_is_read_without_changes(self):
        before = self.path.read_bytes()
        probe.inspect_database(self.path)
        self.assertEqual(before, self.path.read_bytes())

    def test_non_synthetic_or_unexpected_counts_rejected(self):
        db = sqlite3.connect(self.path)
        db.execute('UPDATE samples SET synthetic=0')
        db.commit()
        db.close()
        before = self.path.read_bytes()
        with self.assertRaises(probe.Stop) as result:
            probe.inspect_database(self.path)
        self.assertEqual(result.exception.status, 'CONTENT')
        self.assertEqual(before, self.path.read_bytes())

    def test_missing_or_invalid_database_fails_without_creation(self):
        missing = self.fixture_dir / 'missing.sqlite3'
        with self.assertRaises(probe.Stop) as result:
            probe.inspect_database(missing)
        self.assertEqual(result.exception.status, 'QUERY')
        self.assertFalse(missing.exists())

    def test_journal_present_or_unknown_fails_closed(self):
        with patch.object(probe.os, 'stat', side_effect=FileNotFoundError):
            probe.no_journal_companions(7)
        for side_effect in (None, PermissionError):
            with self.subTest(side_effect=side_effect), patch.object(probe.os, 'stat', side_effect=side_effect):
                with self.assertRaises(probe.Stop) as result:
                    probe.no_journal_companions(7)
                self.assertEqual(result.exception.status, 'COMPANION')

    def test_metadata_rejects_unsafe_modes_owners_types_links_and_sizes(self):
        cases = [
            ('root', stat.S_IFDIR | 0o755, 1, 1, 123, True),
            ('root', stat.S_IFDIR | 0o775, 1, 1, 123, False),
            ('child', stat.S_IFDIR | 0o700, 1, 1, 123, True),
            ('child', stat.S_IFLNK | 0o700, 1, 1, 123, False),
            ('child', stat.S_IFDIR | 0o755, 1, 1, 123, False),
            ('database', stat.S_IFREG | 0o600, 1, 32, 123, True),
            ('database', stat.S_IFLNK | 0o600, 1, 32, 123, False),
            ('database', stat.S_IFREG | 0o600, 2, 32, 123, False),
            ('database', stat.S_IFREG | 0o640, 1, 32, 123, False),
            ('database', stat.S_IFREG | 0o600, 1, 32, 124, False),
            ('database', stat.S_IFREG | 0o600, 1, probe.MAX_DB_BYTES + 1, 123, False),
        ]
        for kind, mode, links, size, owner, valid in cases:
            info = SimpleNamespace(st_mode=mode, st_nlink=links, st_size=size, st_uid=owner)
            with self.subTest(kind=kind, mode=mode, owner=owner):
                if valid: probe.require_metadata(info, 123, kind)
                else:
                    with self.assertRaises(probe.Stop): probe.require_metadata(info, 123, kind)

    def test_acl_absence_required_and_unknown_stops(self):
        with patch.object(probe.os, 'getxattr', side_effect=OSError(errno.ENODATA, 'absent'), create=True):
            probe.no_posix_acl(7, ('system.posix_acl_access',))
        for value in (b'present', PermissionError()):
            with self.subTest(value=value):
                with patch.object(probe.os, 'getxattr', return_value=value, side_effect=value if isinstance(value, Exception) else None, create=True):
                    with self.assertRaises(probe.Stop) as result:
                        probe.no_posix_acl(7, ('system.posix_acl_access',))
                    self.assertEqual(result.exception.status, 'ACL')

    def test_supervisor_enforces_mode_strips_secret_and_bounded_run(self):
        env = {'MOM_HEALTH_SELFTEST':'1', 'MOM_HEALTH_ALLOW_REAL_DATA':'false',
               'MOM_HEALTH_VOLUME_MOUNT':'/data',
               'MOM_HEALTH_SYNTHETIC_DB_PATH':'/data/mom-health-private/mom-health-synthetic.sqlite3',
               'MOM_HEALTH_UPLOAD_TOKEN':'should-not-be-inherited'}
        seen = []
        def runner(args, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(returncode=0)
        self.assertEqual(supervisor.run(env, runner), 'PASS')
        self.assertNotIn('MOM_HEALTH_UPLOAD_TOKEN', seen[0]['env'])
        self.assertEqual(seen[0]['timeout'], 25)
        self.assertEqual(supervisor.run({**env, 'MOM_HEALTH_ALLOW_REAL_DATA':'true'}, runner), 'MODE')
        self.assertEqual(supervisor.run(env, lambda *a, **k: SimpleNamespace(returncode=12)), 'ACL')
        def timeout(*args, **kwargs): raise subprocess.TimeoutExpired('probe', 25)
        self.assertEqual(supervisor.run(env, timeout), 'TIMEOUT')

    def test_in_memory_sample_and_status_checks(self):
        for sample_flag, status_flag in ((1, None), (0, None), (1, 0), (1, 1)):
            with self.subTest(sample_flag=sample_flag, status_flag=status_flag):
                db = sqlite3.connect(':memory:')
                try:
                    db.executescript('CREATE TABLE samples (synthetic INTEGER);'
                                     'CREATE TABLE batches (id INTEGER);'
                                     'CREATE TABLE gateway_status (synthetic INTEGER);'
                                     'INSERT INTO batches VALUES (1); INSERT INTO batches VALUES (2);')
                    db.execute('INSERT INTO samples VALUES (?)', (sample_flag,))
                    if status_flag is not None:
                        db.execute('INSERT INTO gateway_status VALUES (?)', (status_flag,))
                    db.commit()
                    if sample_flag == 1 and status_flag is None:
                        probe.inspect_connection(db)
                    else:
                        with self.assertRaises(probe.Stop) as result:
                            probe.inspect_connection(db)
                        self.assertEqual(result.exception.status, 'CONTENT')
                finally:
                    db.close()


if __name__ == '__main__': unittest.main()
