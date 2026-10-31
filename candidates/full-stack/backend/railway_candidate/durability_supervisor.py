"""Run a bounded synthetic durability probe with fixed-category output."""
import os
import subprocess
import sys

STATUS = {0: 'PASS', 10: 'MODE', 11: 'METADATA', 12: 'ACL', 13: 'COMPANION',
          14: 'DATABASE', 15: 'QUERY', 16: 'CONTENT'}


def run(environ, runner=subprocess.run):
    if (environ.get('MOM_HEALTH_SELFTEST') != '1' or
            environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false' or
            environ.get('MOM_HEALTH_VOLUME_MOUNT') != '/data' or
            environ.get('MOM_HEALTH_SYNTHETIC_DB_PATH') != '/data/mom-health-private/mom-health-synthetic.sqlite3'):
        return 'MODE'
    child_env = {name: environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'PATH') if name in environ}
    child_env.update({'MOM_HEALTH_SELFTEST': '1', 'MOM_HEALTH_ALLOW_REAL_DATA': 'false',
                      'MOM_HEALTH_VOLUME_MOUNT': '/data',
                      'MOM_HEALTH_SYNTHETIC_DB_PATH': '/data/mom-health-private/mom-health-synthetic.sqlite3',
                      'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'})
    try:
        result = runner([sys.executable, '-B', '-m', 'railway_candidate.durability_probe'],
                        env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=25, check=False, shell=False, close_fds=True)
        return STATUS.get(result.returncode, 'UNKNOWN')
    except subprocess.TimeoutExpired:
        return 'TIMEOUT'
    except Exception:
        return 'UNKNOWN'


def main():
    result = run(os.environ)
    print('DURABILITY ' + ('PASS' if result == 'PASS' else 'STOP: ' + result), flush=True)
    return 0 if result == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
