"""Read-only probe of the fixed, previously-created synthetic SQLite database.

No listener is opened and no credentials or health values are queried or emitted. The URI uses
SQLite immutable mode only after refusing all known journal companions. This
does not defend against same-UID or privileged concurrent path replacement.
"""
import errno
import os
from pathlib import Path
import sqlite3
import stat

from railway_candidate.inspect_volume import mountinfo_has_target

ROOT = Path('/data')
CHILD = 'mom-health-private'
DB = 'mom-health-synthetic.sqlite3'
MAX_DB_BYTES = 4 * 1024 * 1024
STATUS = {'PASS': 0, 'MODE': 10, 'METADATA': 11, 'ACL': 12,
          'COMPANION': 13, 'DATABASE': 14, 'QUERY': 15, 'CONTENT': 16}


class Stop(Exception):
    def __init__(self, status):
        self.status = status


def no_posix_acl(fd, names):
    if not hasattr(os, 'getxattr'):
        raise Stop('ACL')
    for name in names:
        try:
            os.getxattr(fd, name)
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise Stop('ACL') from None
        except (TypeError, NotImplementedError):
            raise Stop('ACL') from None
        else:
            raise Stop('ACL')


def no_journal_companions(child_fd):
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            os.stat(DB + suffix, dir_fd=child_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except (OSError, TypeError, NotImplementedError):
            raise Stop('COMPANION') from None
        raise Stop('COMPANION')


def require_metadata(info, uid, kind):
    if info.st_uid != uid:
        raise Stop('METADATA')
    if kind == 'root':
        valid = stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022
    elif kind == 'child':
        valid = stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o077
    elif kind == 'database':
        valid = (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                 not info.st_mode & 0o077 and 0 < info.st_size <= MAX_DB_BYTES)
    else:
        valid = False
    if not valid:
        raise Stop('METADATA')


def inspect_connection(db):
    db.set_progress_handler(lambda: 1, 100000)
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA trusted_schema=OFF')
    integrity = db.execute('PRAGMA quick_check(1)').fetchone()
    if integrity != ('ok',):
        raise Stop('DATABASE')
    counts = db.execute('SELECT COUNT(*), SUM(CASE WHEN synthetic = 1 THEN 1 ELSE 0 END) FROM samples').fetchone()
    batch_count = db.execute('SELECT COUNT(*) FROM batches').fetchone()
    status_counts = db.execute('SELECT COUNT(*), SUM(CASE WHEN synthetic = 1 THEN 1 ELSE 0 END) FROM gateway_status').fetchone()
    if counts != (1, 1) or batch_count != (2,) or status_counts != (0, None):
        raise Stop('CONTENT')


def inspect_database(path):
    """Inspect only aggregate synthetic flags and integrity; never read values."""
    uri = path.as_uri() + '?mode=ro&immutable=1'
    try:
        db = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            inspect_connection(db)
        finally:
            db.close()
    except Stop:
        raise
    except (sqlite3.Error, OSError, ValueError):
        raise Stop('QUERY') from None


def inspect(root=ROOT):
    """Validate metadata without mutation, then reopen SQLite in immutable mode."""
    if os.name != 'posix' or not all(hasattr(os, name) for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'geteuid')):
        raise Stop('METADATA')
    root_fd = child_fd = db_fd = None
    try:
        if root.resolve(strict=True) != root or not os.path.ismount(root):
            raise Stop('METADATA')
        if not mountinfo_has_target(Path('/proc/self/mountinfo').read_text(encoding='utf-8'), root):
            raise Stop('METADATA')
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root_info = os.fstat(root_fd)
        require_metadata(root_info, os.geteuid(), 'root')
        no_posix_acl(root_fd, ('system.posix_acl_access', 'system.posix_acl_default'))
        child_fd = os.open(CHILD, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        child_info = os.fstat(child_fd)
        require_metadata(child_info, os.geteuid(), 'child')
        no_posix_acl(child_fd, ('system.posix_acl_access', 'system.posix_acl_default'))
        no_journal_companions(child_fd)
        db_fd = os.open(DB, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=child_fd)
        info = os.fstat(db_fd)
        require_metadata(info, os.geteuid(), 'database')
        no_posix_acl(db_fd, ('system.posix_acl_access',))
        inspect_database(root / CHILD / DB)
        no_journal_companions(child_fd)
    except Stop:
        raise
    except (OSError, ValueError, TypeError, NotImplementedError):
        raise Stop('METADATA') from None
    finally:
        for fd in (db_fd, child_fd, root_fd):
            if fd is not None:
                os.close(fd)


def main():
    if (os.environ.get('MOM_HEALTH_SELFTEST') != '1' or
            os.environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false' or
            os.environ.get('MOM_HEALTH_VOLUME_MOUNT') != '/data' or
            os.environ.get('MOM_HEALTH_SYNTHETIC_DB_PATH') != '/data/' + CHILD + '/' + DB):
        return STATUS['MODE']
    try:
        inspect()
    except Stop as exc:
        return STATUS.get(exc.status, STATUS['QUERY'])
    except Exception:
        return STATUS['QUERY']
    return STATUS['PASS']


if __name__ == '__main__':
    raise SystemExit(main())
