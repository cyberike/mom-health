"""Run the private synthetic temporary-fixture harness with bounded lifetime."""
import os
import subprocess
import sys


def run(environ, runner=subprocess.run):
    if (environ.get('MOM_HEALTH_SELFTEST') != '1' or
            environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false' or
            environ.get('MOM_HEALTH_VOLUME_MOUNT') != '/data'):
        return 'MODE'
    child_env = {key:environ[key] for key in ('SYSTEMROOT','WINDIR','PATH') if key in environ}
    child_env.update({'MOM_HEALTH_SELFTEST':'1', 'MOM_HEALTH_ALLOW_REAL_DATA':'false',
                      'MOM_HEALTH_VOLUME_MOUNT':'/data', 'PYTHONDONTWRITEBYTECODE':'1',
                      'PYTHONNOUSERSITE':'1'})
    try:
        child = runner([sys.executable, '-B', '-m', 'railway_candidate.ephemeral_harness'],
                       env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=25, check=False, shell=False, close_fds=True)
        return {0:'PASS',10:'MODE',11:'CHECK_FAILED'}.get(child.returncode, 'UNKNOWN')
    except subprocess.TimeoutExpired:
        return 'TIMEOUT'
    except Exception:
        return 'UNKNOWN'


def main():
    result = run(os.environ)
    print('EPHEMERAL INTEGRATION ' + ('PASS' if result == 'PASS' else 'STOP: ' + result), flush=True)
    return 0 if result == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
