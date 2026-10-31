# Historical deferred acceptance checklist

This document records an earlier build-only stage and is retained for history. Its opening statement and authorization timing are superseded by later work: several local synthetic checks, private Railway synthetic tests, and supervised user-led tests have since occurred. Consult `PROGRESS_20261029_FOLLOWUP.md`, `PROGRESS_20261029_SECURITY_REVIEW.md`, `DELIVERY_0.8.3.md` and `PHONE_ACCEPTANCE_0.8.3.md` for the current evidence and remaining gates. The 0.8.3 candidate still has not been physically accepted, and real-data transmission and phone activation require the applicable separate approval.

This candidate has not been run. Compiling is not a passing test. Use synthetic data first, independently accept each component, and only then approve candidate integration testing. Keep the working 0.5.0 APK and recoverable source checkpoint available. The candidate uses a separate package/database and must not replace the working release.

## 1. Pure protocol and state checks (15–25 minutes)

Compile and execute the existing protocol/display/session regression entry points under tests only after test execution is authorized. Add the prepared history checks: exact static/dynamic record sizes, malformed BCD dates, unknown terminal fragments, zero/out-of-range HR, device/session mismatch, capacity and unsupported model gates. Device-local history dates must not silently acquire UTC or a guessed sampling interval. No live commands are needed.

## 2. Android data component (30–45 minutes)

Run tests/data/DataComponentChecks.java with an approved Android-compatible runtime because Android org.json stubs cannot execute on an ordinary JDK. Exercise an isolated disposable SQLite database with synthetic rows: recording off rejects append; exact duplicate is idempotent; changed duplicate is rejected; capacity 10,000 rejects without eviction; page boundaries and device/session IDs remain intact; partial acknowledgment marks only committed rows; malformed/foreign acknowledgments retain pending rows; explicit deletion cannot be acknowledged by a stale response. No health data or watch is required.

## 3. Backend component (30–45 minutes, PostgreSQL additional)

Use the deferred unittest command in backend/README.md only after approval. Validate rollback, restart/replay, collisions, policy rejection, pagination, bounds, loopback denial and token gates with synthetic fixtures. PostgreSQL needs an approved instance and the currently absent driver before parity/concurrency tests; do not substitute successful SQLite tests for PostgreSQL evidence.

## 4. Candidate UI and client interoperability (30–45 minutes)

Only after separate installation/runtime approval: first launch must leave recording off, endpoint/token blank, database unopened and network idle. Confirm V4/history controls report unavailable without writes. At large fonts inspect all buttons, forms, dialogs and paginated history. Start a synthetic upload through an approved isolated HTTPS fixture; verify one request, redirects rejected, partial acknowledgments, timeout, cancellation during connect/read/commit and explicit retry. Backend launcher intentionally only accepts loopback; phone-to-service TLS/access arrangements are not configured by this build. Do not bypass that boundary.

Leave and reenter the Activity: recording is off, token cleared, upload cancelled, no automatic request or measurement. Repeat while an operation is queued, while uploading, and after server commit before response. Pending rows may remain after ambiguous cancellation; a retry must not duplicate server samples. Verify no stale UI callbacks from the previous visit. Clear saved data only through the explicit confirmation and after upload cancellation completes.

## 5. User-led hardware acceptance (20–30 minutes per model)

After the previous chunks pass and installation/device interaction is separately authorized, compare the same checklist on Pixel 10 and Fold 6: model selection, known watch, battery/firmware, one 30-second HR request, manual Stop, timeout, screen-awake behavior, foreground exit, manual reconnect, cleared old reading and no automatic measurement. Saving is a distinct optional action and upload is a further explicit action. Verify no recording while off and session/device provenance when on. No medical accuracy inference. Mom's S25 Ultra remains untested. V1 and V4 must not inherit V8 compatibility; V4 and watch-history acquisition remain gated until documented uncertainties are resolved.

## Release gate

Do not promote this APK because it compiles. Record each chunk's actual outcome and remaining failures. Candidate runtime, persistence, cancellation, HTTPS interoperability, PostgreSQL and physical acceptance are all pending. Public deployment, ongoing credentials, multi-user access, retention/backups and real-data upload require separate decisions. These have not been activated.
