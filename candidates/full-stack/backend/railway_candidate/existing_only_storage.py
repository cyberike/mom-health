"""Noncreating validation for a disposable synthetic database fixture.

No mkdir, file creation, truncation or SQLite connection occurs here. On
POSIX, fixed directory-relative no-follow opens and metadata/ACL checks are
used. Windows checks are only for local synthetic fixtures and do not prove
Windows ACL isolation. Same-UID or privileged replacement after validation
remains out of scope; SQLite reopens the path later.
"""
import errno
import os
from pathlib import Path
import stat

from railway_candidate.gateway import _require_no_posix_acl


def _require_no_file_acl(fd):
    if not hasattr(os, 'getxattr'):
        raise ValueError('POSIX file ACL status unavailable')
    try:
        os.getxattr(fd, 'system.posix_acl_access')
    except OSError as exc:
        if exc.errno != errno.ENODATA:
            raise ValueError('POSIX file ACL status unavailable') from None
    except (TypeError, NotImplementedError):
        raise ValueError('POSIX file ACL status unavailable') from None
    else:
        raise ValueError('POSIX ACL present on storage file')


def validate_existing_storage(mount_path, database_path, *, private_child=False):
    """Validate an existing fixed-path fixture without ever creating it."""
    if not isinstance(private_child, bool):
        raise ValueError('Invalid private-child storage configuration')
    root, db = Path(mount_path or ''), Path(database_path or '')
    parent = root / 'mom-health-private' if private_child else root
    if (not root.is_absolute() or not db.is_absolute() or db.parent != parent or
            db.name != 'mom-health-synthetic.sqlite3'):
        raise ValueError('Invalid existing synthetic fixture path')
    if os.name == 'nt' and (len(root.drive) != 2 or root.drive[1] != ':' or
                            not root.drive[0].isascii() or not root.drive[0].isalpha() or
                            db.drive.casefold() != root.drive.casefold()):
        # Reject literal UNC/device paths before resolving. A drive letter can
        # still be mapped to a remote filesystem; its locality is not verified.
        raise ValueError('Synthetic fixture requires a drive-letter path')
    opened = []
    try:
        if root.resolve(strict=True) != root or not root.is_dir() or not os.path.ismount(root):
            raise ValueError('Configured storage must be an existing non-symlink mount')
        if os.name == 'posix':
            if not all(hasattr(os, name) for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK', 'geteuid')):
                raise ValueError('POSIX no-follow support unavailable')
            dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            root_fd = os.open(root, dir_flags)
            opened.append(root_fd)
            root_info = os.fstat(root_fd)
            unsafe_bits = 0o022 if private_child else 0o077
            if root_info.st_uid != os.geteuid() or root_info.st_mode & unsafe_bits:
                raise ValueError('Storage root ownership or mode is unsafe')
            _require_no_posix_acl(root_fd)
            parent_fd = root_fd
            if private_child:
                parent_fd = os.open('mom-health-private', dir_flags, dir_fd=root_fd)
                opened.append(parent_fd)
                info = os.fstat(parent_fd)
                if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ValueError('Private child ownership or mode is unsafe')
                _require_no_posix_acl(parent_fd)
            # Reject a FIFO/device/symlink before open. O_NONBLOCK prevents a
            # swapped-in FIFO from hanging this validation; the matching fstat
            # checks below also reject a changed inode. Trusted same-UID/admin
            # control is still required for pathname reuse after validation.
            before = os.stat(db.name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_uid != os.geteuid() or before.st_mode & 0o077):
                raise ValueError('Synthetic fixture ownership, mode or type is unsafe')
            file_fd = os.open(db.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            opened.append(file_fd)
            info = os.fstat(file_fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.geteuid() or info.st_mode & 0o077 or
                    (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
                raise ValueError('Synthetic fixture ownership, mode or type is unsafe')
            _require_no_file_acl(file_fd)
        elif os.name == 'nt':
            # Local Windows synthetic fixture only. These metadata checks do not
            # establish ACL isolation and are not a public hosting guarantee.
            if private_child:
                raise ValueError('Private child validation requires POSIX support')
            root_info = root.lstat()
            info = db.lstat()
            reparse_point = 0x400
            if (getattr(root_info, 'st_file_attributes', 0) & reparse_point or
                    getattr(info, 'st_file_attributes', 0) & reparse_point or
                    not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('Synthetic fixture type or path is unsafe')
        else:
            raise ValueError('Unsupported fixture platform')
    except OSError as exc:
        raise ValueError('Existing synthetic fixture unavailable or unsafe') from exc
    finally:
        for fd in reversed(opened):
            os.close(fd)
    return str(db)
