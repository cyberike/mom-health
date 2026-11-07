# Mom Health backend candidate

Verification at the synthetic-only batch policy update included 24 backend tests (current counts and results: ../REVIEW_20261103_RESILIENCE_TESTS.md); earlier integration checks are described in INTEGRATION_RESULT.md and did not run against that update. The build-only statements below describe the earlier initial build pass; they are not the current test status. Hosting, phone transport and real-data activation remain unverified/inactive. PostgreSQL has run only against throwaway local PostgreSQL 16 clusters with fictional data (`tests/postgresql_parity.py`), never a hosted or managed instance.

Original implementation staged independently from the working Android release. No tests, application imports, migrations, database creation, services, or network operations were executed in this build pass. Test code is prepared, not verified.

## Implemented source

- Strict shared v1 sample/batch contract, timezone-aware timestamps normalized to UTC, opaque device IDs, nullable HR with missing reasons, no unsupported metric fields.
- Transactional SQLite and PostgreSQL repositories, versioned schema, globally unique sample IDs, collision rejection, idempotent durable batch receipts, partial acknowledgments only after successful commit. For accepted batches, replayed batch IDs return the original committed acknowledgment; changed content is HTTP 409.
- In synthetic-only mode, any batch containing a sample marked non-synthetic is rejected in full before storage is opened. Real-marked samples get `real_data_disabled`; synthetic samples in that mixed batch get `batch_contains_real_data` and remain pending. The response includes submitted IDs in memory but no receipt, batch ID, sample ID or digest from that rejected batch is persisted by this path. These rejection responses are deterministic for exact repeats but do not reserve the batch ID or provide collision detection against previously stored batch IDs. Pure synthetic batches retain their durable idempotency. This change does not remove receipts written by older versions and does not authenticate the sender's `synthetic` label or erase data in intermediary logs.
- Bounded request body (128 KiB), batches (100), pagination (100), device-bound keyset cursors. No unbounded read endpoint.
- POST /v1/batches: malformed requests receive 422 before mutation; oversized body 413; unavailable storage 503 without acknowledgment. Collision reason: sample_id_collision.
- GET /v1/samples?device_id=opaque-id&limit=25&cursor=... returns {samples:[contract samples], next_cursor:string|null}. Pagination follows insertion order, not inferred measurement time. Concurrent appends may appear on later pages.
- GET /v1/health returns readiness after schema initialization, schema_version and synthetic_only. This is process readiness, not a continuous database probe.
- POST /v1/gateway-status keeps the last received synthetic report and the last received real report per device; reported_at order is not enforced, so a delayed older report still replaces a newer one. GET /v1/gateway-status?device_id=... serves the real gateway's report whenever one exists in real-data mode (a synthetic test report never hides it) and synthetic reports only in synthetic-only mode.
- Every response the application sends, including boundary rejections (400/401/403/413), carries no-store, nosniff, no-referrer, DENY framing and a restrictive Content-Security-Policy. Responses produced outside it do not: Starlette's plain 500 for an unhandled exception and Uvicorn's own 400 for a malformed HTTP request.
- Loopback-only launcher and request boundary. Proxy forwarding headers disabled. Synthetic-only default. Real-data mode refuses startup without an explicitly supplied access token; all endpoints require that token when configured. Remote peers always rejected even with a token. No credentials generated or configured. No CORS enabled.

## Local test and service commands

From this backend directory, to run the synthetic tests:

```powershell
python -m unittest discover -s tests -v
```

To start a synthetic local service when that operation is authorized:

```powershell
python -m mom_health_backend
```

This binds 127.0.0.1:8765 and creates mom-health-synthetic.sqlite3 in the current directory at startup. Importing the package does not create a database. The launcher is intentionally not configurable to bind externally. API access logging and interactive docs are off.

Environment settings read only when constructing the app: MOM_HEALTH_SQLITE_PATH, MOM_HEALTH_DATABASE_URL (PostgreSQL connection string), MOM_HEALTH_ALLOW_REAL_DATA (exact true/false; default false), MOM_HEALTH_ACCESS_TOKEN. Do not place secrets in command lines or committed files. No values are supplied here. PostgreSQL adapter imports psycopg lazily and applies migrations within a transaction. Ingestion is serialized by a PostgreSQL transaction advisory lock (gateway status reports by a second one), intentionally favoring correctness over throughput for this prototype. Each PostgreSQL connection pins READ COMMITTED and a 5 s lock timeout, like SQLite's busy timeout, so a stuck lock holder is answered 503 rather than holding a worker indefinitely.

## Checks and blockers

The unittest source covers acceptance/replay, duplicate/collision, conflicting batch reuse, atomic policy rejection of mixed batches, device pagination isolation, transaction rollback, malformed values, unsupported metrics, nullable values, request bounds, loopback denial and explicit authorization. The 24 backend tests passed with generated synthetic fixtures after the policy change. PostgreSQL parity, concurrency, rollback, crash-recovery and lock-timeout checks are in `tests/postgresql_parity.py`; it is opt-in and is run through `tests/postgresql_throwaway.py`, which creates a socket-only throwaway cluster (it needs Linux, the PostgreSQL server binaries and the psycopg in requirements-postgresql.txt; its own behavior is checked by the opt-in `tests/postgresql_launcher_checks.py`). Given only MOM_HEALTH_TEST_POSTGRESQL_DSN for another disposable database, the parity module runs wherever psycopg does, and its crash check is skipped. Docker server access was previously denied to the parent; no retry was made. Requirements are manifests, not installation actions.

Real-data/public activation is not ready: user authorization, authentication ownership and rotation, per-user isolation, TLS/hosting, retention/deletion policy, backup/encryption and operational capacity decisions remain external prerequisites. Current code is single-principal local-only. There is deliberately no automatic pruning of idempotency receipts: deleting them without a defined retention protocol could re-admit duplicates. Disk capacity must be assessed before persistent activation; request memory and page sizes are bounded, but total durable storage is not capped. No real health data was ingested or transmitted.

No clinical accuracy claim follows from accepting a well-formed sample. Populated non-synthetic HR is restricted to live V1/V8 provenance; V4 and history may carry nullable unavailable states, but not populated real HR. Synthetic fixtures can exercise future capability paths. This is an evidence gate, not certification of hardware accuracy.

Static wire review: Android emits canonical UUID identifiers and UTC Instant timestamps. Server canonicalization accepts up to nine fractional digits, preserves nanosecond differences and normalizes equivalent timezone offsets/trailing fraction zeros before duplicate comparisons. Both input and normalized UTC years must fit 1..9999. Tokens are constrained to 32..4096 bearer-token characters, with whitespace/control characters rejected. Backend tests cover these boundaries. This does not establish phone-to-server compatibility.
