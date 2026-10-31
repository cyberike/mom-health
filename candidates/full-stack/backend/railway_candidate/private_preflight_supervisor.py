"""Bounded read-only metadata probe. Only fixed labels can reach logs."""
import json
import os
import subprocess
import sys

from railway_candidate.private_preflight import FIELDS

ALLOWED = {
    'mount': {'YES', 'NO', 'UNKNOWN'}, 'mountinfo': {'YES', 'NO', 'UNKNOWN'},
    'owner': {'YES', 'NO', 'UNKNOWN'}, 'other_writable': {'YES', 'NO', 'UNKNOWN'},
    'child': {'ABSENT', 'REGULAR', 'DIRECTORY', 'SYMLINK', 'OTHER', 'UNKNOWN'},
    'legacy_db': {'ABSENT', 'REGULAR', 'DIRECTORY', 'SYMLINK', 'OTHER', 'UNKNOWN'},
    'access_acl': {'ABSENT', 'PRESENT', 'UNKNOWN'}, 'default_acl': {'ABSENT', 'PRESENT', 'UNKNOWN'},
}
SAFE = {'mount':'YES', 'mountinfo':'YES', 'owner':'YES', 'other_writable':'NO',
        'child':'ABSENT', 'legacy_db':'ABSENT', 'access_acl':'ABSENT', 'default_acl':'ABSENT'}


def run(environ, runner=subprocess.run):
    if (environ.get('MOM_HEALTH_SELFTEST') != '1' or
            environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false' or
            environ.get('MOM_HEALTH_VOLUME_MOUNT') != '/data'):
        return 'PREFLIGHT REJECTED: CONFIGURATION'
    child_env = {k: environ[k] for k in ('SYSTEMROOT', 'WINDIR', 'PATH') if k in environ}
    child_env.update({'MOM_HEALTH_SELFTEST':'1', 'MOM_HEALTH_ALLOW_REAL_DATA':'false',
                      'MOM_HEALTH_VOLUME_MOUNT':'/data', 'PYTHONDONTWRITEBYTECODE':'1',
                      'PYTHONNOUSERSITE':'1'})
    try:
        result = runner([sys.executable, '-B', '-m', 'railway_candidate.private_preflight'],
                        env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, timeout=25, check=False, shell=False, close_fds=True)
        if result.returncode != 0 or len(result.stdout) > 4096:
            return 'PREFLIGHT FAILED: METADATA_UNAVAILABLE'
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or set(data) != FIELDS or any(data[k] not in ALLOWED[k] for k in FIELDS):
            return 'PREFLIGHT FAILED: METADATA_UNAVAILABLE'
    except subprocess.TimeoutExpired:
        return 'PREFLIGHT FAILED: TIMEOUT'
    except Exception:
        return 'PREFLIGHT FAILED: METADATA_UNAVAILABLE'
    summary = ' '.join(k.upper() + '=' + data[k] for k in sorted(FIELDS))
    return 'PREFLIGHT ' + ('PASS' if data == SAFE else 'STOP') + ': ' + summary


def main():
    output = run(os.environ)
    print(output, flush=True)
    return 0 if output.startswith('PREFLIGHT PASS:') else 1


if __name__ == '__main__':
    raise SystemExit(main())
