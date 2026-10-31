"""Bound one private synthetic self-test subprocess; never opens a listener.

Child output is deliberately discarded. This is not an OS network sandbox,
and process termination does not prove Railway stopped or disabled restarts.
"""
import os
from pathlib import Path
import subprocess
import sys

TIMEOUT_SECONDS = 25
# Fixed exit statuses only; never read or relay child output or exception text.
CHILD_STATUS = {10: 'MODE_REJECTED', 11: 'PATH_INVALID', 12: 'DB_EXISTS',
                13: 'STORAGE_MOUNT_INVALID', 14: 'STORAGE_PERMISSIONS',
                15: 'STORAGE_UNAVAILABLE', 16: 'STORAGE_DB_INVALID',
                17: 'GATEWAY_INIT_FAILED', 18: 'CHECK_FAILED', 19: 'EXECUTION_FAILED',
                20: 'DEPENDENCY_IMPORT_FAILED', 21: 'CHILD_EXISTS',
                22: 'STORAGE_ACL_UNAVAILABLE', 23: 'STORAGE_ACL_PRESENT'}


def run_supervisor(environ, runner=subprocess.run):
    if environ.get('MOM_HEALTH_SELFTEST') != '1' or environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false':
        return 'MODE_REJECTED'
    root = Path(environ.get('MOM_HEALTH_VOLUME_MOUNT', ''))
    db = Path(environ.get('MOM_HEALTH_SYNTHETIC_DB_PATH', ''))
    private_child = environ.get('MOM_HEALTH_PRIVATE_CHILD', 'false')
    if private_child not in ('true', 'false'):
        return 'MODE_REJECTED'
    expected_parent = root / 'mom-health-private' if private_child == 'true' else root
    if not root.is_absolute() or not db.is_absolute() or db.parent != expected_parent or db.name != 'mom-health-synthetic.sqlite3':
        return 'PATH_INVALID'
    # Keep real service credentials and proxy settings out of the self-test child.
    child_env = {key: environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH') if key in environ}
    child_env.update({'MOM_HEALTH_SELFTEST': '1', 'MOM_HEALTH_ALLOW_REAL_DATA': 'false',
                      'MOM_HEALTH_VOLUME_MOUNT': str(root), 'MOM_HEALTH_SYNTHETIC_DB_PATH': str(db),
                      'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                      'MOM_HEALTH_PRIVATE_CHILD': private_child})
    # Do not inherit arbitrary PYTHONPATH or secret-bearing user environment.
    try:
        result = runner([sys.executable, '-m', 'railway_candidate.selftest'],
                        env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, check=False,
                        shell=False, close_fds=True)
        return 'PASS' if result.returncode == 0 else CHILD_STATUS.get(result.returncode, 'CHILD_UNCLASSIFIED')
    except subprocess.TimeoutExpired:
        return 'CHILD_TIMEOUT'
    except OSError:
        return 'CHILD_LAUNCH_FAILED'


def main():
    try:
        result = run_supervisor(os.environ)
    except Exception:
        # Never forward unexpected exception text or traceback to deployment logs.
        result = 'SUPERVISOR_INTERNAL_ERROR'
    if result == 'PASS':
        print('SUPERVISOR PASS: synthetic self-test exited successfully; no ingress or TLS claim', flush=True)
        return 0
    print('SUPERVISOR FAIL: ' + result + '; inspect privately before retry', flush=True)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
