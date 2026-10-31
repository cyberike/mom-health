# Mom Health: independent chunks for Saturday, October 24

## Brief and evidence actually reviewed

The canonical brief is in the initial parent delegation: one Android BLE gateway independent of vendor consumer cloud; per-model DeviceAdapters; nullable normalized health data; offline buffering; historical retrieval only where supported; batch upload; battery/connection/sync status; later FastAPI/Python/PostgreSQL. The first milestone is real watch -> our code -> a verified sensor value. No separate original brief/backlog file was found among the project-root documents. README.md, PROTOCOL.md, V8_FINDINGS.md, V8_SHUTDOWN_REVIEW.md, source, tests and saved verification records were reviewed against that delegated brief. No additional product sections were assumed.

Parent reports 0.5.0 Pixel initial HR, sleep/manual reconnect with cleared old reading and no automatic measurement, manual Stop and subsequent bounded HR all working. Fold 6 HR/expected behavior broadly confirmed. 0.4.2 awake behavior confirmed. These are user reports, not new executions in this task. Accuracy remains unverified; S25 Ultra is confirmed as Mom's phone but untested. Earlier build records correctly describe their original build-time status; this paragraph supplies the later evidence.

## Dependency order and delivery gates

0. Preserve a recoverable source checkpoint and verify 0.5.0 and 0.4.2 APK hashes. Completed before changes. Source ZIP excludes signing keys, downloaded SDKs, build intermediates and real health data. Recovery: extract into a separate review directory first; do not overwrite working source blindly.
1. Normalized contract and capability gates (20-30 minute initial estimate): contract.py. Immutable synthetic HR candidate, explicit nullable reason, phone receipt timestamp with timezone, model/session/source provenance. V8 user-reported transport evidence is distinct from accuracy; V1 source-only; V4 unknown. History remains disabled. Completed and independently tested.
2. Bounded memory buffer, depends on 1 (25-35 minute initial estimate): memory_buffer.py. Explicit enqueue only, capacity rejection without eviction, stable batch order, pending-ID duplicate/conflict handling and validated partial acknowledgment. No disk/background activity or collection. Completed and independently tested.
3. Future batch boundary, depends on 1 (20-30 minute initial estimate): batch_contract.py. Strict schema/version/field checking; null semantics preserved; unknown measurements rejected. Pure function, no FastAPI server, database, endpoint, credential or upload. Completed and independently tested.
4. Synthetic-only integration, depends on 2 and 3: demonstrates that validation is not a commit, malformed batches retain pending data, and only simulated confirmed IDs are removed. Completed after individual suites passed. This is not integration into the Android release.
5. Later device history: source evidence and synthetic parser fixtures first, then separately authorized hardware acceptance per model. No history read/delete command or firmware assumption is introduced now. Scope and estimate depend on documented supported formats.
6. Later durable buffer and FastAPI/PostgreSQL ingest: requires a separate decision on real-data retention, encryption/access, deletion, destination/authentication, server-side idempotency, and explicit upload behavior. Current authorization covers code-only synthetic scaffolding, not activation. No service/dependency installed. Estimate after those choices; not a Saturday release dependency.
7. Saturday release candidate: retain the last hardware-passing app. Integrate a chunk only after its software suite passes, any activation scope is approved, and its user-led acceptance passes. No untested chunk is compiled into the app. Keep 0.5.0 and 0.4.2 as fallbacks.

## Current result and limitations

50 unittest cases now pass: contract 10, buffer 11, batch 10, review regressions 16, synthetic integration 3. Tests include subcases for malformed/zero/unknown values, nulls, history gating, capacity, conflicting IDs, partial/invalid acknowledgments, retry retention and strict batch validation. No Java/app source, manifest or build script changed. These Python modules are outside src and are not APK inputs. No gateway APK was produced and no Downloads files changed. A separate zero-permission help-only preview APK was built in candidates/help-only, without integration into the gateway.

Scaffolding is intentionally non-production: memory is lost on process exit; deduplication covers pending items only, not acknowledged IDs; synthetic markers are caller declarations, not detection of real data; no authentication, transport, durable commit, database, clinical validation or automatic data generation exists. Receipt time is not device sample time. It does not claim buffering survives an offline app restart. A backend adapter and production privacy/security design would be separate work.

## Independent review and acceptance

Run from staged_contracts: python -m unittest -v test_chunks.ContractTests; then BufferTests; then BatchTests; then IntegrationTests. Or python staged_contracts/verify.py runs those in order and stops on failure. All fixtures are explicitly synthetic. Inspect a null sample versus an HR candidate; fill a one-item buffer and verify no eviction; validate a batch without acknowledging it; then simulate a partial commit and verify the remaining item stays pending. This requires no watch or network.

When user returns: keep phone tests separate from these scaffolds. On Pixel and Fold, reconfirm 0.5.0 connect -> explicit measure -> Stop/timeout -> sleep -> manual reconnect; verify stale value clearing, no automatic measurement, normal screen behavior, and readable large-font controls. On S25 Ultra, do a separately authorized installation/compatibility check before treating it as supported. Do not add unsupported sensors or clinical alerts for the birthday deadline.

## Bounded quality pass and independent help chunk

Completed review fixes: explicit synthetic device identity; single device/model/session binding per buffer and batch, retained even after acknowledgment; read-only capacity; bounded consumption of acknowledgment iterators; atomic rejection of invalid/partial unknown acknowledgments; UTC-normalized receipt timestamps with overflow rejection and bounded incoming timestamp strings; immutable capability-evidence map; malformed null-reason rejection. Sample field bounds cap accepted item size; pending-item count is capped at 1000. This is application-level boundedness, not a claim of protection against arbitrary hostile Python objects or a deployed HTTP endpoint. IDs after confirmed removal are not durably deduplicated. Clock-skew/future-time adjudication is deliberately not invented: receive time is provenance and FIFO is insertion order, not clinical/sample-time ordering.

The 16 review regressions and existing suites all pass (50 tests total). A pre-review ZIP preserves the earlier staged contract. Schema additions are incompatible with the prior synthetic fixtures; nothing is deployed, so there is no production migration. No Android gateway source/build changes were made.

Independent help chunk: candidates/help-only. Seven plain-language sections: before beginning, connecting, measuring, interpreting current/stale/ended, returning after sleep, troubleshooting, and local-only limitations. This is an original zero-permission help-only native APK with a distinct package, not a competing gateway. Nine content integrity checks plus compilation/signature/alignment passed; actual visual QA remains pending. User acceptance estimate 5-10 minutes, or source-only wording review without installation. The source and APK are separate from 0.5.0 and were not copied to Downloads. Integrate only after acceptance.

## Full brief coverage still outstanding

- Gateway independent of vendor consumer cloud: working V8 foreground path; network-free app.
- Per-model adapters and identical comparisons: V8 user-tested; V1 source/tests only; V4 not implemented. Equivalent physical V1/V4 trials remain pending device access/approval.
- Nullable normalized data: staged synthetic HR contract only; no extra sensor claims.
- Offline buffering: bounded memory prototype only; durable offline buffering is not implemented.
- Historical retrieval: disabled; needs source-supported parser and separately approved per-model hardware tests.
- Batch upload and sync status: validation prototype only; no transport, acknowledgment service or actual sync UI exists.
- FastAPI/Python/PostgreSQL: pure Python contract prepared, but no framework service, database or deployment exists. New activation needs retention/access/destination/authentication decisions.
- Saturday integration: keep 0.5.0/0.4.2 unchanged until each new chunk passes its own software and user acceptance. S25 Ultra compatibility and large-font/UI checks remain user-led. HR accuracy remains unverified.
