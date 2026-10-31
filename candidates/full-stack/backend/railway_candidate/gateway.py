"""Synthetic-only Railway ingress candidate. No network activity on import.

This is NOT approved for real health data. Railway public TLS and origin isolation
must be independently verified, along with rate limiting and storage protections.
"""
import base64
import binascii
import errno
import ipaddress
import os
import re
import stat
import threading
import time
from collections import deque
from hmac import compare_digest
from pathlib import Path

from starlette.responses import FileResponse, JSONResponse
from mom_health_backend.app import Settings, create_app

ROOT = Path(__file__).resolve().parent
SHELL = frozenset(('/dashboard/', '/dashboard/styles.css', '/dashboard/core.js', '/dashboard/app.js'))
READ = frozenset(('/v1/health', '/v1/devices', '/v1/samples', '/v1/gateway-status'))
WRITE = frozenset(('/v1/batches', '/v1/gateway-status'))

class FailureLimiter:
    """Fixed-memory per-role failure budget, shared across all callers in one process."""
    def __init__(self, clock=time.monotonic, limit=8, seconds=60):
        self.clock, self.limit, self.seconds = clock, limit, seconds
        self.lock = threading.Lock()
        self.failures = {'caregiver': deque(), 'phone': deque()}

    def check_and_record(self, role, valid):
        with self.lock:
            now = self.clock()
            bucket = self.failures[role]
            while bucket and now - bucket[0] >= self.seconds:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False, max(1, int(self.seconds - (now - bucket[0]) + 0.999))
            if not valid:
                bucket.append(now)
            return True, 0

def trusted_networks(value):
    if not value or len(value) > 2048:
        raise ValueError('Explicit trusted proxy networks required')
    parts = value.split(',')
    if len(parts) > 16:
        raise ValueError('Too many proxy networks')
    try:
        networks = tuple(ipaddress.ip_network(part.strip(), strict=True) for part in parts)
    except ValueError as ex:
        raise ValueError('Invalid trusted proxy network') from ex
    ranges = tuple(ipaddress.ip_network(item) for item in
                   ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
                    '127.0.0.0/8', 'fc00::/7', '::1/128'))
    if any(not any(net.version == allowed.version and net.subnet_of(allowed) for allowed in ranges) for net in networks):
        raise ValueError('Trusted proxies must be scoped to private or loopback networks')
    return networks

def _require_no_posix_acl(fd):
    """Fail closed if a directory ACL is present or cannot be inspected."""
    if not hasattr(os, 'getxattr'):
        raise ValueError('POSIX ACL status unavailable')
    for name in ('system.posix_acl_access', 'system.posix_acl_default'):
        try:
            os.getxattr(fd, name)
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise ValueError('POSIX ACL status unavailable') from None
        except (TypeError, NotImplementedError):
            raise ValueError('POSIX ACL status unavailable') from None
        else:
            raise ValueError('POSIX ACL present on storage directory')


def validate_private_child_storage(mount_path, database_path, *, require_new=False):
    """Synthetic candidate: retain volume root mode; secure a fixed private child.

    This is POSIX-only and still assumes trusted service-user/admin control.
    POSIX ACL checks are limited to Linux xattrs; platform ACLs and durability
    are not independently established. Trusted service-user/admin control is
    still required between metadata checks and later pathname-based SQLite use.
    """
    root, db = Path(mount_path or ''), Path(database_path or '')
    child = root / 'mom-health-private'
    if (not root.is_absolute() or not db.is_absolute() or db.parent != child or
            db.name != 'mom-health-synthetic.sqlite3'):
        raise ValueError('Database must use the fixed synthetic filename in the private child')
    if os.name != 'posix' or not all(hasattr(os, attr) for attr in ('O_DIRECTORY', 'O_NOFOLLOW', 'geteuid')):
        raise ValueError('Private child storage requires POSIX no-follow support')
    root_fd = child_fd = None
    try:
        if root.resolve(strict=True) != root or not os.path.ismount(root):
            raise ValueError('Configured storage must be an existing non-symlink mount')
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root_info = os.fstat(root_fd)
        # A writable parent permits an unrelated user to replace the child.
        if root_info.st_uid != os.geteuid() or root_info.st_mode & 0o022:
            raise ValueError('Volume must be service-owned and not group/other writable')
        # Check both access and inherited default POSIX ACLs on the opened
        # directory itself, immediately before creating anything. Unknown or
        # unsupported xattr behavior is not evidence that ACLs are absent.
        _require_no_posix_acl(root_fd)
        try:
            os.mkdir('mom-health-private', 0o700, dir_fd=root_fd)
        except FileExistsError:
            # The synthetic test requires a fresh child. Do not enter an
            # existing path even if it appeared after a read-only preflight.
            if require_new:
                raise ValueError('Synthetic private child already exists; inspect before any retry') from None
            pass  # Normal reuse still validates the child and its contents below.
        child_fd = os.open('mom-health-private', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        info = os.fstat(child_fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Private child must be service-owned and private (0700)')
        # The child may already exist and have ACLs independent of its parent.
        # Inspect the opened directory before touching its database, whether
        # this is a fresh test or normal reuse.
        _require_no_posix_acl(child_fd)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            fd = os.open(db.name, flags, 0o600, dir_fd=child_fd)
        except FileExistsError:
            if require_new:
                raise ValueError('Synthetic database already exists; inspect before any retry') from None
            info = os.stat(db.name, dir_fd=child_fd, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise ValueError('Existing database must be an owner-private unlinked regular file')
        else:
            os.close(fd)
    except OSError as ex:
        raise ValueError('Synthetic storage unavailable or unsafe') from ex
    finally:
        if child_fd is not None: os.close(child_fd)
        if root_fd is not None: os.close(root_fd)
    return str(db)


def validate_storage(mount_path, database_path, *, require_new=False, private_child=False):
    """Validate private storage; self-test can require atomic exclusive creation."""
    if private_child:
        return validate_private_child_storage(mount_path, database_path, require_new=require_new)
    root = Path(mount_path or '')
    db = Path(database_path or '')
    if not root.is_absolute() or not db.is_absolute() or db.parent != root or db.name != 'mom-health-synthetic.sqlite3':
        raise ValueError('Database must use the fixed synthetic filename directly under the absolute mount path')
    try:
        if root.resolve(strict=True) != root or not root.is_dir() or not os.path.ismount(root):
            raise ValueError('Configured storage must be an existing non-symlink mount')
        info = root.stat()
        if os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('Storage directory must be owned by the service user and private (0700)')
        if require_new:
            # One atomic operation: a concurrent starter or pre-existing path
            # cannot be accepted or overwritten, even between earlier checks.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            try:
                fd = os.open(db, flags, 0o600)
            except FileExistsError:
                raise ValueError('Synthetic database already exists; inspect before any retry') from None
            os.close(fd)
        elif db.exists() or db.is_symlink():
            info = db.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Database must be an unlinked regular file')
            if os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise ValueError('Existing database must be owned by the service user and private (0600)')
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(db, flags, 0o600)
            os.close(fd)
    except OSError as ex:
        raise ValueError('Synthetic storage unavailable or unsafe') from ex
    return str(db)

def create_gateway(host, user, password, upload_token, database_path, mount_path, proxy_cidrs, *, clock=time.monotonic, require_new_database=False, private_child=False):
    """Explicit configuration only. Unit tests use fictional credentials/data."""
    if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host or ''):
        raise ValueError('Exact lowercase DNS hostname required')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', user or ''):
        raise ValueError('Caregiver username invalid')
    if not re.fullmatch(r'[A-Za-z0-9._~+/-]{32,256}', password or ''):
        raise ValueError('Caregiver password must have 32-256 supported characters')
    if not re.fullmatch(r'[A-Za-z0-9._~+/=-]{32,4096}', upload_token or ''):
        raise ValueError('Upload token must have 32-4096 supported characters')
    if compare_digest(password.encode(), upload_token.encode()):
        raise ValueError('Caregiver password and upload token must differ')
    networks = trusted_networks(proxy_cidrs)
    database_path = validate_storage(mount_path, database_path, require_new=require_new_database, private_child=private_child)
    limiter = FailureLimiter(clock=clock)
    # Original backend remains unchanged. Even this hosted candidate refuses
    # real samples: removing that gate requires a separate reviewed change.
    app = create_app(Settings(sqlite_path=database_path, access_token=upload_token, allow_real_data=False))

    async def gateway(scope, receive, send):
        if scope['type'] != 'http':
            return await app(scope, receive, send)
        async def reject(code, message, challenge=False):
            headers = {'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'}
            if challenge: headers['WWW-Authenticate'] = 'Basic realm="Mom Health caregiver", charset="UTF-8"'
            return await JSONResponse({'detail':message}, status_code=code, headers=headers)(scope, receive, send)
        raw = scope.get('headers', [])
        for key in (b'host',b'authorization',b'x-forwarded-proto'):
            if sum(k.lower() == key for k, _ in raw) > 1: return await reject(400,'Ambiguous header')
        headers = {k.lower():v for k,v in raw}
        # Peer comes from the ASGI transport, not client-supplied forward headers.
        try:
            peer = ipaddress.ip_address((scope.get('client') or ('',))[0])
        except ValueError:
            return await reject(403,'Untrusted ingress')
        if not any(peer in network for network in networks):
            return await reject(403,'Untrusted ingress')
        # These are configuration checks, not proof of TLS: this depends on a
        # verified Railway ingress that overwrites client headers and isolates
        # the origin. Do not activate real data based on this header.
        if headers.get(b'host') != host.encode() or headers.get(b'x-forwarded-proto') != b'https':
            return await reject(403,'Expected configured HTTPS ingress')
        path, method = scope.get('path',''), scope.get('method','')
        caregiver = method == 'GET' and (path in SHELL or path in READ)
        phone = method == 'POST' and path in WRITE
        if not caregiver and not phone: return await reject(404,'Not found')
        auth = headers.get(b'authorization',b'')
        if len(auth) > 8192:
            auth = b''
        if caregiver:
            valid = False
            if auth.startswith(b'Basic '):
                try:
                    pair = base64.b64decode(auth[6:], validate=True)
                    valid = compare_digest(pair, (user+':'+password).encode('ascii'))
                except (ValueError,binascii.Error): pass
            role = 'caregiver'
        else:
            role = 'phone'
            valid = compare_digest(auth, b'Bearer '+upload_token.encode('ascii'))
        allowed, retry = limiter.check_and_record(role, valid)
        if not allowed:
            return await JSONResponse({'detail':'Too many authentication failures; retry later'},status_code=429,
                headers={'Cache-Control':'no-store','Retry-After':str(retry),'X-Content-Type-Options':'nosniff'})(scope,receive,send)
        if not valid:
            return await reject(401,'Caregiver access required' if caregiver else 'Upload authorization required',caregiver)
        if path in ('/dashboard/', '/dashboard/app.js'):
            file_name = 'dashboard-index.html' if path == '/dashboard/' else 'dashboard-app.js'
            media_type = 'text/html' if path == '/dashboard/' else 'text/javascript'
            return await FileResponse(ROOT/file_name, media_type=media_type, headers={
                'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
                'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})(scope,receive,send)
        # Authentication already happened above. Translate the now-authorized
        # request to the unmodified local app's trusted internal interface.
        internal=dict(scope)
        internal['client']=('127.0.0.1', 0)
        internal['headers']=[(k,v) for k,v in raw if k.lower()!=b'authorization']+[(b'authorization',b'Bearer '+upload_token.encode('ascii'))]
        return await app(internal,receive,send)
    return gateway

def application():
    if os.environ.get('MOM_HEALTH_ALLOW_REAL_DATA','false') != 'false':
        raise ValueError('Railway candidate is synthetic-only')
    private_child = os.environ.get('MOM_HEALTH_PRIVATE_CHILD', 'false')
    if private_child not in ('true', 'false'):
        raise ValueError('Invalid private-child storage configuration')
    return create_gateway(os.environ.get('MOM_HEALTH_HOSTNAME'), os.environ.get('MOM_HEALTH_CAREGIVER_USER'),
        os.environ.get('MOM_HEALTH_CAREGIVER_PASSWORD'), os.environ.get('MOM_HEALTH_UPLOAD_TOKEN'),
        os.environ.get('MOM_HEALTH_SYNTHETIC_DB_PATH'), os.environ.get('MOM_HEALTH_VOLUME_MOUNT'),
        os.environ.get('MOM_HEALTH_TRUSTED_PROXY_CIDRS'), private_child=private_child == 'true')
