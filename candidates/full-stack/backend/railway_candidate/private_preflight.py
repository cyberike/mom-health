"""Read-only, synthetic-only metadata inspection for the fixed Railway volume.

Never opens either database, modifies the mount or reports metadata values.
Absence of POSIX ACL xattrs is not proof against other access mechanisms.
"""
import errno
import json
import os
from pathlib import Path
import stat

from railway_candidate.inspect_volume import mountinfo_has_target

ROOT = Path('/data')
MOUNTINFO = Path('/proc/self/mountinfo')
FIELDS = frozenset(('mount', 'mountinfo', 'owner', 'other_writable', 'child',
                    'legacy_db', 'access_acl', 'default_acl'))


def _entry(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return 'ABSENT'
    except OSError:
        return 'UNKNOWN'
    if stat.S_ISLNK(mode): return 'SYMLINK'
    if stat.S_ISDIR(mode): return 'DIRECTORY'
    if stat.S_ISREG(mode): return 'REGULAR'
    return 'OTHER'


def _acl(path, name, getxattr=os.getxattr if hasattr(os, 'getxattr') else None):
    if getxattr is None:
        return 'UNKNOWN'
    try:
        getxattr(path, name, follow_symlinks=False)
    except OSError as exc:
        if exc.errno == getattr(errno, 'ENODATA', -1): return 'ABSENT'
        return 'UNKNOWN'
    return 'PRESENT'


def inspect(root=ROOT, mountinfo=MOUNTINFO, *, getxattr=None, geteuid=None, ismount=None):
    """Return only enum labels; no health content, ACL contents, paths or IDs."""
    result = {name: 'UNKNOWN' for name in FIELDS}
    try:
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode): return result
        result['owner'] = 'YES' if info.st_uid == (geteuid or os.geteuid)() else 'NO'
        result['other_writable'] = 'YES' if info.st_mode & 0o022 else 'NO'
        result['mount'] = 'YES' if (ismount or os.path.ismount)(root) else 'NO'
        result['mountinfo'] = 'YES' if mountinfo_has_target(mountinfo.read_text(encoding='utf-8'), root) else 'NO'
        result['child'] = _entry(root / 'mom-health-private')
        result['legacy_db'] = _entry(root / 'mom-health-synthetic.sqlite3')
        getter = getxattr if getxattr is not None else getattr(os, 'getxattr', None)
        result['access_acl'] = _acl(root, 'system.posix_acl_access', getter)
        result['default_acl'] = _acl(root, 'system.posix_acl_default', getter)
    except (OSError, AttributeError, ValueError):
        pass
    return result


def main():
    if (os.name != 'posix' or os.environ.get('MOM_HEALTH_SELFTEST') != '1' or
            os.environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false' or
            os.environ.get('MOM_HEALTH_VOLUME_MOUNT') != '/data'):
        return 2
    try:
        print(json.dumps(inspect(), sort_keys=True), flush=True)
        return 0
    except Exception:
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
