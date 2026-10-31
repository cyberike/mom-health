"""Local synthetic-only direct-auth candidate; it is not a deployment entry point.

This intentionally changes the trust model relative to ``gateway.py``. It does
not authenticate the edge, infer TLS, or use forwarded fields as identity. Do
not expose or activate it without separate security review and authorization.
The in-process controls are not a comprehensive brute-force or DoS defense.
"""
import asyncio
import base64
import binascii
from collections import OrderedDict, deque
import hashlib
import hmac
import math
import re
import secrets
import threading
import time

from starlette.responses import FileResponse, JSONResponse
from mom_health_backend.app import Settings, create_app
from railway_candidate.gateway import ROOT, SHELL, READ, WRITE, validate_storage
from railway_candidate.existing_only_storage import validate_existing_storage
from railway_candidate.budgeted_sqlite import BudgetedSQLiteRepository

MAX_ROTATION_SECONDS = 7 * 24 * 60 * 60
MAX_HEADERS = 64
MAX_HEADER_BYTES = 32768
# Bearer prefix plus the existing backend's maximum 4096-byte token.
MAX_AUTH_BYTES = 4103
MAX_IN_FLIGHT = 32
MAX_BODY_BYTES = 131072
BODY_READ_SECONDS = 10.0


class FingerprintLimiter:
    """Bound repeated invalid credentials, not unique guessing or valid users.

    Fixed-size HMAC fingerprints avoid keeping raw credentials as keys. Entries
    are process-local, evictable, reset on restart and not shared by workers.
    An attacker can vary invalid credentials and evade this repeated-value
    throttle. A separate reviewed ingress-level abuse defense is required.
    """
    def __init__(self, clock=time.monotonic, limit=8, seconds=60, max_entries=512):
        if not 1 <= limit <= 64 or not 1 <= seconds <= 3600 or not 1 <= max_entries <= 4096:
            raise ValueError('Invalid local limiter bounds')
        self.clock, self.limit, self.seconds, self.max_entries = clock, limit, seconds, max_entries
        self.lock = threading.Lock()
        self._key = secrets.token_bytes(32)
        self.entries = OrderedDict()

    def record_invalid(self, role, auth):
        if role not in ('caregiver', 'phone') or not isinstance(auth, bytes) or len(auth) > MAX_AUTH_BYTES:
            raise ValueError('Invalid limiter input')
        fingerprint = hmac.new(self._key, role.encode('ascii') + b'\0' + auth, hashlib.sha256).digest()
        with self.lock:
            now = self.clock()
            bucket = self.entries.get(fingerprint)
            if bucket is None:
                if len(self.entries) >= self.max_entries:
                    self.entries.popitem(last=False)
                bucket = deque()
                self.entries[fingerprint] = bucket
            else:
                self.entries.move_to_end(fingerprint)
            while bucket and now - bucket[0] >= self.seconds:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return max(1, math.ceil(self.seconds - (now - bucket[0])))
            bucket.append(now)
            return 0


def _validate_user(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value):
        raise ValueError('Invalid caregiver username')


def _validate_password(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._~+/-]{32,256}', value):
        raise ValueError('Invalid caregiver password')


def _validate_phone(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{32,4096}', value):
        raise ValueError('Invalid phone token')


def _valid_expiry(value, now):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid previous credential expiry')
    if value > now + MAX_ROTATION_SECONDS:
        raise ValueError('Previous credential overlap exceeds seven days')


def create_direct_candidate(host, caregiver_user, caregiver_password, phone_token,
                            database_path, mount_path, *, caregiver_previous=None,
                            phone_previous=None, clock=time.monotonic, wall_clock=time.time,
                            private_child=False, repository=None,
                            require_existing_storage=False):
    """Create an unexposed synthetic candidate with explicitly supplied secrets.

    Previous credentials, if supplied, must have an absolute expiry and cannot
    overlap for more than seven days from configuration. Removing a previous
    credential from configuration and restarting removes it. Current secrets
    must be independently generated with sufficient entropy by the operator;
    format/length validation alone cannot prove entropy.
    """
    if not isinstance(host, str) or not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host):
        raise ValueError('Exact lowercase DNS hostname required')
    _validate_user(caregiver_user)
    _validate_password(caregiver_password)
    _validate_phone(phone_token)
    if not isinstance(private_child, bool):
        raise ValueError('Invalid private-child storage configuration')
    if not isinstance(require_existing_storage, bool):
        raise ValueError('Invalid existing-storage configuration')
    if require_existing_storage and (type(repository) is not BudgetedSQLiteRepository or
                                     repository.require_existing is not True):
        raise ValueError('Existing-only storage requires its budgeted repository')
    now = wall_clock()
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError('Invalid wall clock')
    caregiver_pairs = [(caregiver_user, caregiver_password, None)]
    phone_tokens = [(phone_token, None)]
    if caregiver_previous is not None:
        if not isinstance(caregiver_previous, (tuple, list)) or len(caregiver_previous) != 3:
            raise ValueError('Invalid previous caregiver configuration')
        prev_user, prev_password, expiry = caregiver_previous
        _validate_user(prev_user)
        _validate_password(prev_password)
        _valid_expiry(expiry, now)
        if (prev_user, prev_password) == (caregiver_user, caregiver_password):
            raise ValueError('Previous caregiver must differ')
        caregiver_pairs.append((prev_user, prev_password, expiry))
    if phone_previous is not None:
        if not isinstance(phone_previous, (tuple, list)) or len(phone_previous) != 2:
            raise ValueError('Invalid previous phone configuration')
        prev_token, expiry = phone_previous
        _validate_phone(prev_token)
        _valid_expiry(expiry, now)
        if hmac.compare_digest(prev_token, phone_token):
            raise ValueError('Previous phone token must differ')
        phone_tokens.append((prev_token, expiry))
    if any(hmac.compare_digest(password, token) for _, password, _ in caregiver_pairs for token, _ in phone_tokens):
        raise ValueError('Caregiver and phone secrets must differ')

    if require_existing_storage:
        database_path = validate_existing_storage(mount_path, database_path, private_child=private_child)
        if repository.path != database_path:
            raise ValueError('Repository path must match validated storage')
    else:
        database_path = validate_storage(mount_path, database_path, private_child=private_child)
    # No environment flag can enable real-data storage here. Tests may inject a
    # synthetic-only repository, but no test repository is installed by default.
    app = create_app(Settings(sqlite_path=database_path, access_token=phone_token, allow_real_data=False), repository=repository)
    limiter = FingerprintLimiter(clock=clock)
    slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)
    wall_lock = threading.Lock()
    # In this process a backward wall-clock change must not revive an expired
    # previous secret. Restart loses the high-water mark; trusted time remains
    # a deployment prerequisite, not something this process can establish.
    wall_high_water = now

    async def gateway(scope, receive, send):
        scope_type = scope.get('type')
        if scope_type == 'lifespan':
            return await app(scope, receive, send)
        if scope_type == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        if scope_type != 'http':
            return
        response_started = False
        async def tracked_send(message):
            nonlocal response_started
            if message.get('type') == 'http.response.start':
                response_started = True
            await send(message)
        async def reject(code, message, challenge=False, retry=0):
            headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                       'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}
            if challenge:
                headers['WWW-Authenticate'] = 'Basic realm="Mom Health caregiver", charset="UTF-8"'
            if retry:
                headers['Retry-After'] = str(retry)
            return await JSONResponse({'detail': message}, status_code=code, headers=headers)(scope, receive, tracked_send)
        if not slots.acquire(blocking=False):
            return await reject(503, 'Service busy; retry later')
        release_on_exit = True
        try:
            async def process():
                nonlocal wall_high_water
                raw = scope.get('headers', [])
                if not isinstance(raw, (tuple, list)) or len(raw) > MAX_HEADERS:
                    return await reject(400, 'Invalid headers')
                if any(not isinstance(pair, (tuple, list)) or len(pair) != 2 or
                       not isinstance(pair[0], bytes) or not isinstance(pair[1], bytes) for pair in raw):
                    return await reject(400, 'Invalid headers')
                if sum(len(key) + len(value) for key, value in raw) > MAX_HEADER_BYTES:
                    return await reject(400, 'Invalid headers')
                for key in (b'host', b'authorization', b'origin', b'content-length', b'transfer-encoding'):
                    if sum(name.lower() == key for name, _ in raw) > 1:
                        return await reject(400, 'Ambiguous header')
                headers = {key.lower(): value for key, value in raw}
                if b'content-length' in headers and b'transfer-encoding' in headers:
                    return await reject(400, 'Ambiguous header')
                if headers.get(b'host') != host.encode('ascii'):
                    return await reject(403, 'Invalid host')
                if b'origin' in headers and headers[b'origin'] != ('https://' + host).encode('ascii'):
                    return await reject(403, 'Invalid origin')
                path, method = scope.get('path', ''), scope.get('method', '')
                raw_path = scope.get('raw_path')
                if not isinstance(path, str) or raw_path is None or raw_path != path.encode('utf-8'):
                    return await reject(404, 'Not found')
                caregiver = method == 'GET' and (path in SHELL or path in READ)
                phone = method == 'POST' and path in WRITE
                if not caregiver and not phone:
                    return await reject(404, 'Not found')
                auth = headers.get(b'authorization', b'')
                if len(auth) > MAX_AUTH_BYTES:
                    return await reject(400, 'Invalid authorization')
                current_time = wall_clock()
                if isinstance(current_time, bool) or not isinstance(current_time, (int, float)) or not math.isfinite(current_time):
                    return await reject(503, 'Service unavailable')
                with wall_lock:
                    if current_time < wall_high_water:
                        clock_reversed = True
                    else:
                        wall_high_water = current_time
                        clock_reversed = False
                if clock_reversed:
                    return await reject(503, 'Service unavailable')
                valid = False
                if caregiver:
                    pair = b''
                    if auth.startswith(b'Basic '):
                        try:
                            pair = base64.b64decode(auth[6:], validate=True)
                        except (ValueError, binascii.Error):
                            pass
                    for user, password, expiry in caregiver_pairs:
                        matched = hmac.compare_digest(pair, (user + ':' + password).encode('ascii'))
                        valid |= matched and (expiry is None or current_time < expiry)
                    role = 'caregiver'
                else:
                    for token, expiry in phone_tokens:
                        matched = hmac.compare_digest(auth, b'Bearer ' + token.encode('ascii'))
                        valid |= matched and (expiry is None or current_time < expiry)
                    role = 'phone'
                if not valid:
                    # Browsers first request the protected page without any
                    # Authorization header. Never suppress their challenge
                    # because unrelated anonymous requests share that value.
                    retry = limiter.record_invalid(role, auth) if auth else 0
                    if retry:
                        return await reject(429, 'Too many repeated failures; retry later', retry=retry)
                    return await reject(401, 'Authorization required', challenge=caregiver)
                # Read an authenticated request body under one absolute
                # deadline. This timeout ends before any backend/DB work starts;
                # it never cancels a synchronous worker after admission.
                body = bytearray()
                loop = asyncio.get_running_loop()
                deadline = loop.time() + BODY_READ_SECONDS
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return await reject(408, 'Request body timed out')
                    try:
                        message = await asyncio.wait_for(receive(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return await reject(408, 'Request body timed out')
                    if not isinstance(message, dict):
                        return await reject(400, 'Invalid request body')
                    if message.get('type') == 'http.disconnect':
                        return
                    if message.get('type') != 'http.request':
                        return await reject(400, 'Invalid request body')
                    chunk = message.get('body', b'')
                    if not isinstance(chunk, bytes):
                        return await reject(400, 'Invalid request body')
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        return await reject(413, 'Request body too large')
                    body.extend(chunk)
                    if not message.get('more_body', False):
                        break
                delivered = False
                async def replay_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                    return await receive()
                if path in ('/dashboard/', '/dashboard/app.js'):
                    file_name = 'dashboard-index.html' if path == '/dashboard/' else 'dashboard-app.js'
                    media = 'text/html' if path == '/dashboard/' else 'text/javascript'
                    return await FileResponse(ROOT / file_name, media_type=media, headers={
                        'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
                        'Referrer-Policy':'no-referrer', 'X-Frame-Options':'DENY',
                        'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})(scope, replay_receive, tracked_send)
                # Forwarding headers are deliberately neither trusted nor
                # passed to the local application. The local backend gets its
                # internal loopback scope and the current internal credential.
                internal = dict(scope)
                internal['client'] = ('127.0.0.1', 0)
                internal['headers'] = [(k.lower(), v) for k, v in raw if k.lower() != b'authorization'
                    and k.lower() != b'forwarded' and not k.lower().startswith(b'x-forwarded-')
                    and k.lower() != b'x-real-ip'] + [(b'authorization', b'Bearer ' + phone_token.encode('ascii'))]
                return await app(internal, replay_receive, tracked_send)
            operation = asyncio.create_task(process())
            try:
                # Keep the slot if the caller is cancelled while downstream
                # work is still completing; don't impose a timeout that could
                # release admission while a synchronous DB worker still runs.
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                release_on_exit = False
                def finished(task):
                    try:
                        task.exception()
                    except BaseException:
                        pass
                    slots.release()
                operation.add_done_callback(finished)
                raise
        finally:
            if release_on_exit:
                slots.release()
    # Expose non-secret test hooks; not a deployment or runtime entry point.
    gateway.limiter = limiter
    return gateway
