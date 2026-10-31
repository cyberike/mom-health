"""Read-only synthetic mount metadata probe. No database is opened or created."""
import os
import importlib
from pathlib import Path
import re
import stat

ROOT = Path('/data')
DB_NAME = 'mom-health-synthetic.sqlite3'
MOUNTINFO = Path('/proc/self/mountinfo')

def mountinfo_has_target(content, target):
    # Linux mountinfo encodes spaces, tabs, newlines and backslashes as octal.
    for line in content.splitlines():
        parts = line.split(' - ', 1)[0].split()
        if len(parts) < 6:
            continue
        decoded = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), parts[4])
        if decoded == str(target):
            return True
    return False

def inspect(root=ROOT, mountinfo=MOUNTINFO):
    """Return bitmask 0..127; exceptions are handled without text by the caller."""
    if os.name != 'posix':
        raise RuntimeError('unsupported platform')
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError('root unavailable')
    result = 0
    if os.path.ismount(root): result |= 1
    if mountinfo_has_target(mountinfo.read_text(encoding='utf-8'), root): result |= 2
    if info.st_uid == os.geteuid(): result |= 4
    if info.st_mode & 0o077 == 0: result |= 8
    try:
        importlib.import_module('fastapi.testclient')
        importlib.import_module('railway_candidate.gateway')
    except Exception:
        pass  # Only a fixed IMPORTS=FAILED status is returned; exception stays private.
    else:
        result |= 64
    try:
        item = (root / DB_NAME).lstat()
    except FileNotFoundError:
        pass
    else:
        result |= 16
        if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
            result |= 32
    return result

def main():
    if os.environ.get('MOM_HEALTH_SELFTEST') != '1' or os.environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false':
        return 2
    if os.environ.get('MOM_HEALTH_VOLUME_MOUNT') != '/data' or os.environ.get('MOM_HEALTH_SYNTHETIC_DB_PATH') != '/data/' + DB_NAME:
        return 3
    try:
        return 128 + inspect()
    except Exception:
        return 4

if __name__ == '__main__':
    raise SystemExit(main())
