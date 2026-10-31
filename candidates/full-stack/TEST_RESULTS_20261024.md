# Synthetic software test results — October 24, 2026

User authorized synthetic software tests only by replying “lets go” to the explicit test-only proposal. No phones, watches, emulators, health data, installs, network listeners or deployed services were used. FastAPI TestClient ran the application in process with generated fixtures; no HTTP server was started.

## Passed

All 10 portable Java suites compiled and completed successfully:

- ProtocolTests: 1,114 assertions.
- V8ProtocolTests: 3,979 assertions (synthetic and previously transcribed screenshot fixtures; no new hardware evidence).
- HrSessionTests: 253 assertions.
- ReadingDisplayTests: 537 assertions.
- ScreenAwakeTests: 22 assertions.
- WatchSelectionTests: 26 assertions.
- V4AdapterChecks: completed; suite does not report an assertion count.
- ModelComparisonChecks: completed; suite does not report an assertion count.
- HistoryComponentTest: 121 checks.
- HistoryTransferPlanTest: completed; suite does not report an assertion count.

Recorded output: `build/synthetic-a0b53aec6a224062a8bc04705ec1cef9/results.json`.

All 13 Python backend tests passed after moving the test fixture directories into the workspace. Coverage includes FastAPI in-process ingest/query, request limits, sanitized validation, authorization, synthetic-mode isolation, SQLite commit/replay/collision behavior, rollback, device pagination, unsupported capabilities and nanosecond timestamp normalization. Output: `build/backend-tests-20261024.log`.

## Initial environment failure and test-only change

The first backend run failed because Python TemporaryDirectory folders under `C:\Users\serge\AppData\Local\Temp\tmp*` returned Windows access denied during database access and cleanup. Those denied paths were not retried or modified manually. The test fixture helper now creates unique synthetic database directories under `build/backend-fixtures/` using ordinary workspace directory creation and retains them for inspection, with no recursive cleanup. All 13 tests then passed. Production app/backend source was not changed. No Android APK was rebuilt or promoted; existing source ZIPs remain historical snapshots predating this test-harness change.

## Still untested / not release-ready

- Android SQLite persistence and JSON acknowledgment execution, real Android lifecycle/cancellation, UI rendering/accessibility and phone-to-backend TLS interoperability. The Android API jar contains stubs, not an Android runtime; passing portable Java checks cannot validate these behaviors. No prior denied emulator authorization was retried.
- PostgreSQL runtime/parity: driver and approved instance remain unavailable; no Docker retry/install.
- V4 hardware and live history acquisition remain gated. Passing synthetic codecs/planners does not establish device compatibility or resolve history terminal/time semantics.
- New candidate physical acceptance, mom's S25 Ultra compatibility and sensor accuracy remain unverified.

Next stage requires separately approved Android runtime/installation and user-led interaction. Keep 0.5.0 as the working fallback; do not replace it solely because these software suites passed.
