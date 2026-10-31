# Build-only delivery: 0.6.0 candidate

Separate candidate, not a replacement for the working 0.5.0 release. No new tests, app runs, services, database creation, phone/emulator actions or uploads occurred. The prior working-release and staged-chunk test results do not validate this candidate.

## Delivered code and dependency order

1. Shared nullable HR/provenance contract: strict IDs, timestamps, missing reasons, capability restrictions and synthetic fixtures. Android/Python formats aligned in static review, including nanosecond timestamp preservation.
2. Android SQLite data component: explicit opt-in, 10,000-record bound with no silent eviction, duplicate/collision checks, local history pagination and pending/uploaded/error status.
3. Backend: FastAPI v1 batch ingestion, health and bounded device-history pagination; SQLite and PostgreSQL schemas/repositories; transactional acknowledgments, durable replay and collision handling; synthetic-only and loopback defaults with explicit token gate for real-data mode. No credentials configured.
4. Android upload component: explicit HTTPS batch request, memory-only optional token, bounded body/response, no redirects/automatic retries, strict partial acknowledgments and cancellation.
5. History component: offline V1/V4/V8 static/dynamic HR decoders and bounded device/session-isolated review. Actual hardware retrieval is incomplete and gated because terminal framing, continuation and timestamp semantics need validation. V4 live measurement is unavailable. No fabricated sensor values.
6. Separate candidate composition: preserved foreground BLE behavior, optional saving, local history/status, explicit upload/clear controls, plain-language help and unavailable capability labels. Data controls start inactive. Leaving clears recording consent/token and cancels upload; source behavior is not runtime-verified.

The earlier CHUNK_PLAN.md sections were: 0 baseline; 1 normalized contract; 2 bounded memory buffer; 3 batch envelope; 4 synthetic integration; 5 history evidence; 6 durable storage/backend activation; 7 release integration. This build supplies durable/API/client source beyond that earlier staged plan. Runtime integration and promotion remain deferred.

## Build evidence

Android sources compiled with installed JDK/API tools, then D8/aapt2 packaging, zip alignment and signing commands completed. No separate signature-verification or runtime test command was run under BUILD ONLY. Compiler warnings concern Java 8 obsolescence and deprecated Android APIs. Python backend and test sources compiled to bytecode with py_compile; application modules were not imported or launched.

APK: build/Mom-Health-0.6.0-candidate-V1-V8.apk

SHA-256: e32e50edff19a66ffbc347888911436e654c765937fca0415d428ab50251e05d

Package: org.momhealth.gateway.candidate. Source manifest includes Bluetooth scan/connect plus INTERNET for the explicitly invoked upload code; no permission was requested or granted on a device. No background service, scheduler, wake lock or automatic reconnect was added. The working release manifest is unchanged. Candidate allowBackup=false; cleartext traffic remains disabled. Debug signing is development packaging, not release readiness.

## Precise remaining blockers

- PostgreSQL runtime verification: psycopg and a test server are absent. Earlier Docker version inspection targeted npipe:////./pipe/dockerDesktopLinuxEngine and returned permission denied. This was a denied read, not a started service; it was not retried. Schema/adapter code is delivered, not executed.
- Watch history and V4 transport: supporting SDK source is present, but safe per-model acquisition/terminal/time semantics and hardware compatibility are unverified. Disabled rather than represented as complete acquisition.
- New runtime/regression/UI/network/database tests: intentionally not executed under the latest BUILD ONLY instruction. Thirteen backend unittest methods plus Android data/history and inherited protocol/state checks are prepared. See ACCEPTANCE.md for independent stages and estimates.
- Actual phone-to-backend operation: no destination, TLS route, access credential or deployment configured. Current backend intentionally accepts loopback only. Public/multi-user service, credential lifecycle, retention, encryption/backups and real-data activation need explicit scope decisions; they are not claimed as implemented production infrastructure.
- V8 accuracy and S25 Ultra compatibility remain unverified. Previous Pixel/Fold HR observations only establish transport for the earlier release.

Expected later acceptance effort: pure checks 15–25 minutes; Android data 30–45; backend SQLite 30–45; UI/client interoperability 30–45; physical checks 20–30 per model, with PostgreSQL setup and history protocol investigation additional. Nothing is being run while the user rests.
