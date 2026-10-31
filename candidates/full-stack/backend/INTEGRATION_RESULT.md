# Local integration completed October 26, 2026 (Denver)

This is new backend/dashboard source work after the immutable 0.7.0 APK and source ZIP. Neither artifact was rebuilt or replaced. No phone action, listening HTTP server, deployment, ongoing credential or real health data was used.

## What now works together

dashboard/integration.test.js runs the actual dashboard app.js with a minimal simulated DOM. Its requests pass over a local subprocess pipe to tests/dashboard_bridge.py, which executes the actual FastAPI routes through TestClient and a fresh SQLite database. Responses are not mocked. This tests the server-to-dashboard contract, not Android serialization/runtime or browser HTTP behavior.

Six passing integration checks cover authentication refusal, authenticated empty state, committing and replaying a 55-observation synthetic batch, gateway status, pagination across 50+5 observations with a nullable HR, collision rejection, real-data policy rejection, and clearing the displayed session. Fixtures use the Android wire contract's UUIDs and nullable fields, but are generated in JavaScript, not by the Android app.

The integration exposed a usability gap: Refresh was disabled whenever there was no selected device. A caregiver connected before the first upload could not refresh to discover the first watch. app.js now enables Refresh in connected mode even with an empty device list. The integration starts empty, ingests data, then discovers it through Refresh without reconnecting.

## Verification

From backend/dashboard:

    node integration.test.js
    node core.test.js
    node app.test.js

From backend:

    python -m unittest discover -s tests -v

Results: 6 integration checks, 37 core/static checks, 7 simulated UI-flow checks, 19 backend tests; all passed. The first integration attempts failed on test punctuation expectations, which were corrected. Node's initial Python subprocess launch was blocked by the restricted process sandbox (EPERM); the same local test ran after explicit tool escalation was approved. No security settings or sandbox-disabling flags were changed.

Synthetic SQLite fixtures are retained under ../build/integration/<unique ID>/ for inspection, never mixed with phone data. The bridge closes on stdin EOF/close, and no HTTP listener remains running.

## Not claimed complete

- Actual browser rendering, keyboard/zoom/accessibility and browser transport remain unverified. Earlier browser IPC denial has not been retried or bypassed.
- Android HTTPS upload and acknowledgments have not been exercised end-to-end.
- PostgreSQL parity still needs an authorized driver/server; previous Docker denial stands.
- There is no hosted URL, configured operational access token, TLS endpoint or real-data activation. The backend still enforces loopback access and synthetic-only default.
- Current UI service/measurement and phone acceptance gaps remain as identified in the delivery audit.

These results supersede historical statements that no backend tests have run. They do not supersede the historical build-only boundary at the time those reports were written.
