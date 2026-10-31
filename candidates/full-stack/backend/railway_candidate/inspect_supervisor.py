"""Finite read-only inspector; child output and credentials are never logged."""
import os
import subprocess
import sys

def run(environ, runner=subprocess.run):
    if (environ.get('MOM_HEALTH_SELFTEST') != '1' or
        environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false' or
        environ.get('MOM_HEALTH_VOLUME_MOUNT') != '/data' or
        environ.get('MOM_HEALTH_SYNTHETIC_DB_PATH') != '/data/mom-health-synthetic.sqlite3'):
        return 'INSPECTION REJECTED: CONFIGURATION'
    child_env = {key: environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH') if key in environ}
    child_env.update({'MOM_HEALTH_SELFTEST':'1', 'MOM_HEALTH_ALLOW_REAL_DATA':'false',
                      'MOM_HEALTH_VOLUME_MOUNT':'/data',
                      'MOM_HEALTH_SYNTHETIC_DB_PATH':'/data/mom-health-synthetic.sqlite3',
                      'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONNOUSERSITE':'1'})
    try:
        result = runner([sys.executable, '-B', '-m', 'railway_candidate.inspect_volume'], env=child_env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=25, check=False, shell=False, close_fds=True)
    except subprocess.TimeoutExpired:
        return 'INSPECTION FAILED: TIMEOUT'
    except Exception:
        return 'INSPECTION FAILED: LAUNCH'
    code = result.returncode
    if not isinstance(code, int) or code < 128 or code > 255 or (code & 32 and not code & 16):
        return 'INSPECTION FAILED: METADATA_UNAVAILABLE'
    flags = code - 128
    db = 'UNSAFE_OR_OTHER' if flags & 32 else 'REGULAR' if flags & 16 else 'ABSENT'
    yes = lambda n: 'YES' if flags & n else 'NO'
    return ('INSPECTION COMPLETE: ISMOUNT=' + yes(1) + ' MOUNTINFO=' + yes(2) +
            ' OWNER_MATCH=' + yes(4) + ' PRIVATE_MODE=' + yes(8) + ' DATABASE=' + db +
            ' IMPORTS=' + ('OK' if flags & 64 else 'FAILED'))

def main():
    try:
        result = run(os.environ)
    except Exception:
        result = 'INSPECTION FAILED: INTERNAL'
    print(result, flush=True)
    return 0 if result.startswith('INSPECTION COMPLETE:') else 1

if __name__ == '__main__':
    raise SystemExit(main())
