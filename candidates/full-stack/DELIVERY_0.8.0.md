# Mom Health 0.8.0 candidate

Separate development candidate; 0.7.0 and previous APKs are preserved. This is not a delivered hosted service or a device-accepted product.

APK: build/Mom-Health-0.8.0-candidate-V1-V8.apk
SHA-256: f6cc7f2113f3063b391ea17ed707625d0155323cfeb293b0bc0ec6d3b4d91aa5
Package: org.momhealth.gateway.candidate, version code 14. Development signed, Android minimum 31.

## Changes

- Separate bounded capture and upload queues keep slow uploads from holding up local recording. Accepted queued readings drain during service shutdown.
- Caregiver setup shows the last successfully saved automatic reading for the current service session, clearly labeled as a past value; it also explains request-budget waiting and upload-off versus unavailable credentials.
- Dashboard clears prior watch data when selection disappears or changes, rejects unsupported real V4/history HR and clarifies that the latest saved receipt is not necessarily the newest measurement. Connected empty-state refresh works.
- Candidate backend has an opt-in same-host HTTPS reverse-proxy configuration, with loopback default, bearer-token requirement and rejection of ambiguous security headers. See backend/deployment/README.md for trust assumptions and missing safeguards.

## Checks

- Android source compilation and prior portable suites pass; 8 synthetic queue assertions and 13 diagnostic checks passed. APK v3 signature, zip alignment, package/version and manifest permissions verified. Full detailed output: build/verification-736e472760e44893b9f4585e0bfa1c8d/results.json.
- Backend: 22 synthetic tests; Android-shaped contract: 5 tests; dashboard: 38 core, 9 simulated UI and 6 cross-component checks. Cross-component exercises actual FastAPI/SQLite and dashboard JS with simulated DOM and local pipe, not Android runtime or browser.
- No physical-device action, installation, live service, external deployment, credential provisioning or real health-data transfer occurred.

## Remaining gates

No private caregiver URL or hosting account has been selected or configured. The proposed Caddy proxy is not installed or exercised; TLS, ingress protection, durable storage, backup, credential lifecycle and account access need an approved hosting decision and verification before real-data use. The backend remains single-principal, not a per-caregiver identity system. PostgreSQL has not been exercised. Prior Docker and browser authorization failures have not been retried or bypassed.

Phone-to-server HTTPS upload has not been tested on Android. Screen-off cadence, reboot/process recovery, reconnection, shutdown, S25 Ultra compatibility and unattended operation need user-led device acceptance. Historic retrieval and V4 capability remain gated by missing protocol evidence and hardware validation. Heart-rate accuracy remains unverified.
