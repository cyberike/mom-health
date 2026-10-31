"""Private, in-process synthetic integration check; never opens a listener.

The test writes a fresh database in the container's /tmp, never to the retained
/data volume. Storage validation is stubbed only in this harness; this does not
test the gateway's actual POSIX storage or public ingress protections.
"""
import base64
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from unittest.mock import patch
from uuid import uuid4

HOST = 'synthetic.example.com'
USER = 'fictional-caregiver'
PASSWORD = 'fictional-caregiver-password-not-for-use-12345'
PHONE = 'fictional-phone-upload-token-not-for-use-67890'
HEADERS = {'host': HOST, 'x-forwarded-proto': 'https'}
BASIC = 'Basic ' + base64.b64encode((USER + ':' + PASSWORD).encode()).decode()
BEARER = 'Bearer ' + PHONE


def require(value):
    if not value:
        raise ValueError('synthetic integration check failed')


def validate_tmp(tmp=Path('/tmp'), data=Path('/data')):
    """Refuse ambiguous paths or a temporary filesystem shared with /data."""
    if os.name != 'posix' or tmp.resolve(strict=True) != tmp or data.resolve(strict=True) != data:
        raise ValueError('unsupported filesystem')
    if tmp == data or data in tmp.parents or tmp in data.parents:
        raise ValueError('overlapping paths')
    if not tmp.is_dir() or not data.is_dir() or not os.path.ismount(data):
        raise ValueError('missing mount')
    if os.stat(tmp).st_dev == os.stat(data).st_dev:
        raise ValueError('shared filesystem')


def fixture_dir():
    validate_tmp()
    root = Path(tempfile.mkdtemp(prefix='mom-health-synthetic-', dir='/tmp'))
    info = root.lstat()
    if (root.parent != Path('/tmp') or not stat.S_ISDIR(info.st_mode) or
            info.st_uid != os.geteuid() or info.st_mode & 0o077 or root.resolve(strict=True) != root):
        raise ValueError('unsafe fixture')
    return root


def run_checks(root):
    """Run fictional requests only; root is an isolated disposable fixture."""
    from fastapi.testclient import TestClient
    from railway_candidate.gateway import application
    root = Path(root)
    database = root / 'mom-health-synthetic.sqlite3'
    require(root.is_absolute() and root.is_dir() and not database.exists() and not database.is_symlink())
    env = {'MOM_HEALTH_ALLOW_REAL_DATA':'false', 'MOM_HEALTH_PRIVATE_CHILD':'true',
           'MOM_HEALTH_HOSTNAME':HOST, 'MOM_HEALTH_CAREGIVER_USER':USER,
           'MOM_HEALTH_CAREGIVER_PASSWORD':PASSWORD, 'MOM_HEALTH_UPLOAD_TOKEN':PHONE,
           'MOM_HEALTH_SYNTHETIC_DB_PATH':str(database), 'MOM_HEALTH_VOLUME_MOUNT':str(root),
           'MOM_HEALTH_TRUSTED_PROXY_CIDRS':'127.0.0.1/32'}
    def storage_stub(mount, path, *, require_new=False):
        require((mount, path, require_new) == (str(root), str(database), False))
        return str(database)
    def client():
        with patch.dict(os.environ, env, clear=True), patch(
                'railway_candidate.gateway.validate_private_child_storage', side_effect=storage_stub):
            app = application()
        return TestClient(app, client=('127.0.0.1', 1234))
    def sample(synthetic=True):
        return dict(sample_id=str(uuid4()), device_id=str(uuid4()), session_id=str(uuid4()),
                    model='V8', source='live', received_at='2026-01-01T00:00:00Z',
                    measured_at=None, heart_rate_bpm=70, missing_reason=None, synthetic=synthetic)
    count = 0
    def check(value):
        nonlocal count
        require(value)
        count += 1
    read = {**HEADERS, 'authorization': BASIC}
    write = {**HEADERS, 'authorization': BEARER}
    first = sample()
    first_batch = dict(schema_version=1, batch_id=str(uuid4()), samples=[first])
    with client() as c:
        check(c.get('/dashboard/', headers=HEADERS).status_code == 401)
        check(c.get('/dashboard/', headers=read).status_code == 200)
        check(c.get('/dashboard/app.js', headers=HEADERS).status_code == 401)
        js = c.get('/dashboard/app.js', headers=read)
        check(js.status_code == 200 and 'synthetic-only mode' in js.text)
        check(c.get('/dashboard/core.js', headers=HEADERS).status_code == 401)
        check(c.get('/dashboard/core.js', headers=read).status_code == 200)
        check(c.get('/dashboard/styles.css', headers=HEADERS).status_code == 401)
        check(c.get('/dashboard/styles.css', headers=read).status_code == 200)
        check(c.get('/v1/health', headers=read).json().get('synthetic_only') is True)
        check(c.get('/v1/health', headers=write).status_code == 401)
        check(c.post('/v1/batches', headers=read, json=first_batch).status_code == 401)
        first_reply = c.post('/v1/batches', headers=write, json=first_batch)
        check(first_reply.status_code == 200 and first_reply.json()['accepted_ids'] == [first['sample_id']])
        check(c.post('/v1/batches', headers=write, json=first_batch).json() == first_reply.json())
        # Collision and a fresh synthetic sample in one batch exercise partial
        # acknowledgement. A retry returns the same durable receipt.
        collision = dict(first, heart_rate_bpm=71)
        second = sample()
        partial = dict(schema_version=1, batch_id=str(uuid4()), samples=[collision, second])
        reply = c.post('/v1/batches', headers=write, json=partial)
        body = reply.json()
        check(reply.status_code == 200 and body['accepted_ids'] == [second['sample_id']] and
              body['rejected'] == [{'sample_id':first['sample_id'], 'reason':'sample_id_collision'}])
        check(c.post('/v1/batches', headers=write, json=partial).json() == body)
        real_marked, other = sample(False), sample()
        mixed = dict(schema_version=1, batch_id=str(uuid4()), samples=[real_marked, other])
        rejected = c.post('/v1/batches', headers=write, json=mixed)
        check(rejected.status_code == 200 and rejected.json()['accepted_ids'] == [] and
              {r['reason'] for r in rejected.json()['rejected']} == {'real_data_disabled','batch_contains_real_data'})
        check(c.post('/v1/batches', headers=write, json=mixed).json() == rejected.json())
    with client() as c:
        rows = c.get('/v1/samples?device_id=' + first['device_id'], headers=read)
        check(rows.status_code == 200 and [x['sample_id'] for x in rows.json()['samples']] == [first['sample_id']])
    with closing(sqlite3.connect(database)) as db:
        check(db.execute('SELECT COUNT(*) FROM samples').fetchone() == (2,))
        check(db.execute('SELECT COUNT(*) FROM batches').fetchone() == (2,))
        check(db.execute('SELECT COUNT(*) FROM batches WHERE batch_id=?', (mixed['batch_id'],)).fetchone() == (0,))
    with patch.dict(os.environ, {**env, 'MOM_HEALTH_ALLOW_REAL_DATA':'true'}, clear=True):
        try:
            application()
        except ValueError:
            check(True)
        else:
            raise ValueError('real mode accepted')
    return count


def main():
    if (os.environ.get('MOM_HEALTH_SELFTEST') != '1' or
            os.environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false' or
            os.environ.get('MOM_HEALTH_VOLUME_MOUNT') != '/data'):
        return 10
    try:
        run_checks(fixture_dir())
    except Exception:
        return 11
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
