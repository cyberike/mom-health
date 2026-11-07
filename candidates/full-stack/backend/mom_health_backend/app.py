import ipaddress
import os
import re
from pathlib import Path
from typing import Literal
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hmac import compare_digest

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from starlette.concurrency import run_in_threadpool

from .models import Batch, GatewayStatus, device_identifier
from .repository import BatchConflict, PostgreSQLRepository, SQLiteRepository


@dataclass(frozen=True)
class Settings:
    sqlite_path: str = "mom-health-synthetic.sqlite3"
    database_url: str | None = None
    allow_real_data: bool = False
    access_token: str | None = None
    max_body_bytes: int = 131072
    hosted_hostname: str | None = None

    def __post_init__(self):
        if self.allow_real_data and (not self.access_token or len(self.access_token) < 32):
            raise ValueError("Real-data mode requires an explicitly supplied access token of at least 32 characters")
        if self.access_token is not None and (not 32 <= len(self.access_token) <= 4096 or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", self.access_token)):
            raise ValueError("Access token must have 32..4096 valid bearer-token characters")
        if not 1024 <= self.max_body_bytes <= 1048576:
            raise ValueError("Invalid request bound")
        if self.hosted_hostname is not None:
            host = self.hosted_hostname
            if len(host) > 253 or not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host):
                raise ValueError("Hosted hostname must be a lowercase DNS name without a port")
            if not self.access_token:
                raise ValueError("Hosted mode requires an access token, even for synthetic data")

    @classmethod
    def environment(cls):
        raw = os.environ.get("MOM_HEALTH_ALLOW_REAL_DATA", "false")
        if raw not in ("false", "true"):
            raise ValueError("MOM_HEALTH_ALLOW_REAL_DATA must be true or false")
        return cls(sqlite_path=os.environ.get("MOM_HEALTH_SQLITE_PATH", "mom-health-synthetic.sqlite3"), database_url=os.environ.get("MOM_HEALTH_DATABASE_URL"), allow_real_data=raw == "true", access_token=os.environ.get("MOM_HEALTH_ACCESS_TOKEN"), hosted_hostname=os.environ.get("MOM_HEALTH_HOSTED_HOSTNAME") or None)


SECURITY_HEADERS = [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"), (b"content-security-policy", b"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")]


class Boundary:
    """Loopback-only, optional authorization, bounded body before JSON decoding."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        async def private_send(message):
            if message["type"] == "http.response.start":
                message = dict(message, headers=list(message.get("headers", [])) + SECURITY_HEADERS)
            await send(message)
        async def reject(code, detail):
            # Rejections carry the same no-store, nosniff, framing and CSP
            # headers as every other response.
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, private_send)
        host = (scope.get("client") or ("",))[0]
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            return await reject(403, "Loopback access only")
        raw_headers = scope.get("headers", [])
        # Reject ambiguous security-sensitive fields rather than relying on
        # whichever duplicate happens to win when converted to a dictionary.
        for field in (b"host", b"authorization", b"x-forwarded-proto"):
            if sum(name == field for name, _ in raw_headers) > 1:
                return await reject(400, "Duplicate security header")
        headers = dict(raw_headers)
        if self.settings.hosted_hostname is not None:
            # Only a trusted reverse proxy on the same host may reach this loopback service.
            # The proxy MUST replace, not append, the forwarded scheme header.
            if headers.get(b"host", b"") != self.settings.hosted_hostname.encode("ascii") or headers.get(b"x-forwarded-proto", b"") != b"https":
                return await reject(403, "Hosted access requires the configured HTTPS origin")
        token = self.settings.access_token
        shell = scope.get("method") in ("GET", "HEAD") and scope.get("path") in ("/dashboard/", "/dashboard/styles.css", "/dashboard/core.js", "/dashboard/app.js")
        if token and not shell and not compare_digest(headers.get(b"authorization", b""), ("Bearer " + token).encode("ascii")):
            return await reject(401, "Authorization required")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.settings.max_body_bytes:
                return await reject(413, "Request body too large")
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, bounded_receive, private_send)


def create_app(settings=None, repository=None):
    settings = settings or Settings.environment()
    repository = repository or (PostgreSQLRepository(settings.database_url) if settings.database_url else SQLiteRepository(settings.sqlite_path))

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(repository.initialize)
        yield

    app = FastAPI(title="Mom Health local candidate", version="1", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Boundary, settings=settings)

    dashboard=Path(__file__).resolve().parent.parent / "dashboard"

    @app.get("/dashboard/")
    def dashboard_shell():
        return FileResponse(dashboard / "index.html", media_type="text/html")

    @app.get("/dashboard/{asset}")
    def dashboard_asset(asset: str):
        types={"styles.css":"text/css", "core.js":"text/javascript", "app.js":"text/javascript"}
        if asset not in types:
            raise HTTPException(404, "Asset not found")
        return FileResponse(dashboard / asset, media_type=types[asset])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo potentially sensitive submitted values.
        return JSONResponse({"detail": "Invalid request; check schema, bounds, identifiers and timestamps"}, status_code=422)

    @app.get("/v1/health")
    def health():
        return {"status": "ready", "schema_version": 1, "synthetic_only": not settings.allow_real_data}

    @app.post("/v1/batches")
    def ingest(batch: Batch):
        try:
            return repository.ingest(batch, settings.allow_real_data)
        except BatchConflict:
            raise HTTPException(409, "Batch ID reused with different content") from None
        except Exception:
            raise HTTPException(503, "Storage unavailable; no acknowledgment issued") from None

    @app.get("/v1/samples")
    def samples(device_id: str = Query(max_length=64), limit: int = Query(default=25, ge=1, le=100), cursor: str | None = Query(default=None, max_length=256), order: Literal["asc", "desc"] = "asc"):
        try:
            device_identifier(device_id)
            return repository.page(device_id, limit, cursor, allow_real=settings.allow_real_data, order=order)
        except ValueError:
            raise HTTPException(422, "Invalid device identifier or cursor") from None
        except Exception:
            raise HTTPException(503, "Storage unavailable") from None

    @app.get("/v1/devices")
    def devices(limit: int = Query(default=50, ge=1, le=100), cursor: str | None = Query(default=None, max_length=64)):
        try:
            return repository.devices(limit,cursor,allow_real=settings.allow_real_data)
        except ValueError:
            raise HTTPException(422,"Invalid device cursor or limit") from None
        except Exception:
            raise HTTPException(503,"Storage unavailable") from None

    @app.post("/v1/gateway-status")
    def post_status(status:GatewayStatus):
        try:
            return repository.save_status(status,settings.allow_real_data)
        except ValueError:
            raise HTTPException(403,"Real-data mode is disabled") from None
        except Exception:
            raise HTTPException(503,"Status storage unavailable") from None

    @app.get("/v1/gateway-status")
    def get_status(device_id:str=Query(max_length=64)):
        try:
            device_identifier(device_id)
            return repository.status(device_id,settings.allow_real_data)
        except ValueError:
            raise HTTPException(422,"Invalid device identifier") from None
        except Exception:
            raise HTTPException(503,"Status storage unavailable") from None

    return app
