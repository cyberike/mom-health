"""Finite, no-listener synthetic test for an explicitly provisioned volume.

Run only in a dedicated synthetic service. Transport peer and credentials below
are fictional in-process fixtures; this proves neither ingress nor TLS. A test
database is created only if absent and is never deleted or overwritten.
"""
import base64
import os
from pathlib import Path
from uuid import uuid4

HOST = 'synthetic.example.com'
USER = 'synthetic-caregiver'
PASSWORD = 'fictional-caregiver-password-not-for-use-12345'
PHONE = 'fictional-phone-upload-token-not-for-use-67890'
HEADERS = {'host': HOST, 'x-forwarded-proto': 'https'}
BASIC = 'Basic ' + base64.b64encode((USER + ':' + PASSWORD).encode('ascii')).decode('ascii')
BEARER = 'Bearer ' + PHONE


EXIT_CODES = {'MODE_REJECTED': 10, 'PATH_INVALID': 11, 'DB_EXISTS': 12,
              'STORAGE_MOUNT_INVALID': 13, 'STORAGE_PERMISSIONS': 14,
              'STORAGE_UNAVAILABLE': 15, 'STORAGE_DB_INVALID': 16,
              'GATEWAY_INIT_FAILED': 17, 'CHECK_FAILED': 18,
              'EXECUTION_FAILED': 19, 'DEPENDENCY_IMPORT_FAILED': 20,
              'CHILD_EXISTS': 21, 'STORAGE_ACL_UNAVAILABLE': 22,
              'STORAGE_ACL_PRESENT': 23}


class DiagnosticFailure(ValueError):
    """Only predefined categories are exposed; exception details stay private."""
    def __init__(self, category):
        if category not in EXIT_CODES:
            raise ValueError('Unsupported diagnostic category')
        self.category = category
        super().__init__(category)


def _initialization_category(exc):
    # Match exact, static application messages only. Never relay exception text.
    if isinstance(exc, ValueError):
        known = {
            'Configured storage must be an existing non-symlink mount': 'STORAGE_MOUNT_INVALID',
            'Storage directory must be owned by the service user and private (0700)': 'STORAGE_PERMISSIONS',
            'Existing database must be owned by the service user and private (0600)': 'STORAGE_PERMISSIONS',
            'Synthetic storage unavailable or unsafe': 'STORAGE_UNAVAILABLE',
            'Database must be an unlinked regular file': 'STORAGE_DB_INVALID',
            'Database must use the fixed synthetic filename directly under the absolute mount path': 'PATH_INVALID',
            'Synthetic database already exists; inspect before any retry': 'DB_EXISTS',
            'Synthetic private child already exists; inspect before any retry': 'CHILD_EXISTS',
            'POSIX ACL status unavailable': 'STORAGE_ACL_UNAVAILABLE',
            'POSIX ACL present on storage root': 'STORAGE_ACL_PRESENT',
            'POSIX ACL present on storage directory': 'STORAGE_ACL_PRESENT',
            'Database must use the fixed synthetic filename in the private child': 'PATH_INVALID',
            'Private child storage requires POSIX no-follow support': 'STORAGE_UNAVAILABLE',
            'Volume must be service-owned and not group/other writable': 'STORAGE_PERMISSIONS',
            'Private child must be service-owned and private (0700)': 'STORAGE_PERMISSIONS',
        }
        return known.get(str(exc), 'GATEWAY_INIT_FAILED')
    return 'GATEWAY_INIT_FAILED'


def require(condition):
    if not condition:
        raise DiagnosticFailure('CHECK_FAILED')


def run_selftest(mount_path, database_path, *, private_child=False):
    """Returns an integer check count; raises without exposing values or secrets."""
    try:
        from fastapi.testclient import TestClient
        from railway_candidate.gateway import create_gateway
    except ImportError:
        # Any ImportError in this import chain, not proof of a particular missing package.
        raise DiagnosticFailure('DEPENDENCY_IMPORT_FAILED') from None
    root, db = Path(mount_path or ''), Path(database_path or '')
    expected_parent = root / 'mom-health-private' if private_child else root
    if not root.is_absolute() or not db.is_absolute() or db.parent != expected_parent:
        raise DiagnosticFailure('PATH_INVALID')
    # Never reuse, overwrite or delete an existing file, even a previous test.
    if db.exists() or db.is_symlink():
        raise DiagnosticFailure('DB_EXISTS')
    try:
        app = create_gateway(HOST, USER, PASSWORD, PHONE, str(db), str(root), '127.0.0.1/32', require_new_database=True, private_child=private_child)
    except Exception as exc:
        raise DiagnosticFailure(_initialization_category(exc)) from None
    count = 0
    with TestClient(app, client=('127.0.0.1', 12345)) as client:
        def call(method, path, auth=None, **kwargs):
            return client.request(method, path, headers={**HEADERS, **({'authorization': auth} if auth else {})}, **kwargs)
        def check(value):
            nonlocal count
            require(value)
            count += 1

        check(call('GET', '/dashboard/').status_code == 401)
        check(call('GET', '/dashboard/', BEARER).status_code == 401)
        check(call('GET', '/dashboard/', BASIC).status_code == 200)
        check(call('GET', '/v1/health', BASIC).json().get('synthetic_only') is True)
        check(call('GET', '/v1/health', BEARER).status_code == 401)
        check(call('POST', '/v1/batches', BASIC, json={}).status_code == 401)
        check(call('GET', '/not-a-route', BASIC).status_code == 404)
        item = dict(sample_id=str(uuid4()), device_id=str(uuid4()), session_id=str(uuid4()),
                    model='V8', source='live', received_at='2026-01-01T00:00:00Z',
                    measured_at=None, heart_rate_bpm=70, missing_reason=None, synthetic=True)
        batch = dict(schema_version=1, batch_id=str(uuid4()), samples=[item])
        reply = call('POST', '/v1/batches', BEARER, json=batch)
        check(reply.status_code == 200 and reply.json()['accepted_ids'] == [item['sample_id']])
        check(call('POST', '/v1/batches', BEARER, json=batch).json() == reply.json())
        query = '/v1/samples?device_id=' + item['device_id']
        rows = call('GET', query, BASIC)
        check(rows.status_code == 200 and len(rows.json()['samples']) == 1)
        rejected = dict(item, sample_id=str(uuid4()), synthetic=False)
        reply = call('POST', '/v1/batches', BEARER, json=dict(schema_version=1, batch_id=str(uuid4()), samples=[rejected]))
        check(reply.status_code == 200 and reply.json()['rejected'][0]['reason'] == 'real_data_disabled')
        check(len(call('GET', query, BASIC).json()['samples']) == 1)
    require(db.is_file())
    return count


def main():
    try:
        if os.environ.get('MOM_HEALTH_SELFTEST') != '1' or os.environ.get('MOM_HEALTH_ALLOW_REAL_DATA', 'false') != 'false':
            raise DiagnosticFailure('MODE_REJECTED')
        private_child = os.environ.get('MOM_HEALTH_PRIVATE_CHILD', 'false')
        if private_child not in ('true', 'false'):
            raise DiagnosticFailure('MODE_REJECTED')
        checks = run_selftest(os.environ.get('MOM_HEALTH_VOLUME_MOUNT'), os.environ.get('MOM_HEALTH_SYNTHETIC_DB_PATH'), private_child=private_child == 'true')
    except DiagnosticFailure as exc:
        print('SELFTEST FAIL: ' + exc.category, flush=True)
        return EXIT_CODES[exc.category]
    except Exception:
        # Do not emit exception details: they can include sensitive paths or values.
        print('SELFTEST FAIL: EXECUTION_FAILED', flush=True)
        return EXIT_CODES['EXECUTION_FAILED']
    print('SELFTEST PASS: %d in-process synthetic checks; mount validation ran; no ingress or TLS claim' % checks, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
