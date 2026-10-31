# Mom Health 0.7.0 candidate

Packaged separately from the working 0.5.0 and 0.4.2 fallbacks. Package: org.momhealth.gateway.candidate, version code 13, minimum Android API 31, target 35. Development certificate; not a production release.

## Artifacts

- build/Mom-Health-0.7.0-candidate-V1-V8.apk
- APK SHA-256: 8b6c5c962de97af4c641e6354526f015d2a6409e00153e56a5609e26d3920cbc
- Verification: build/verification-08598991b0b04738b16d9f80414b8ea4/results.json
- Build commands/log: build.py and build/build-0.7.0-candidate-log.json
- Repeat local synthetic verification: python verify_candidate.py

## Completed recovery and checks

Read-only inspection confirmed the interrupted CaregiverService edit did not apply. Completed acquisition-only deferral, bounded 60-second CPU wake coverage, and corrected manual help text to describe the separate opt-in caregiver service honestly. The measurement maximum is still 30 seconds. All candidate Android sources compiled. Fourteen Java check suites passed, including 219 automation policy and 24 caregiver settings/ownership assertions. Three suites report pass without assertion totals; the others report 6,314 assertions combined. APK v3 signature and zip alignment passed. Manifest/version/permissions were inspected. Compilation emitted Java 8 obsolescence and existing deprecated API warnings, no errors.

Existing unchanged dashboard evidence: 44 synthetic/static checks. Existing unchanged backend evidence: 19 synthetic tests, recorded in build/dashboard-backend-tests.log. These were not rerun in this recovery pass. No browser visual QA or physical-phone QA is claimed.

## Current code coverage

- Manual live V1/V8 transport: MainActivity, BleController, V1Adapter, V8Adapter, HrSession.
- Optional hands-off caregiver operation: SERVICE_COMPONENT.md and the Caregiver* classes; automation/AutomationEngine and AutomationConfig.
- Local buffered data and explicit acknowledgments: data/HealthStore, HealthSample, BatchUploader, SyncCoordinator.
- Private dashboard and backend code: backend/dashboard and backend/mom_health_backend; source only, not deployed.
- Evidence-gated history and V4: history package, V4Adapter, HISTORY_COMPONENT.md, V4_COMPONENT.md. Missing transport/record semantics and hardware acceptance remain gates; unavailable features are not represented as working sensors.

The user's explicit private-dashboard and hands-off requirements supersede earlier audit wording that those features were outside the original brief. This candidate contains their implementation, but is not proof of a reliable deployed hands-off system.

## Remaining acceptance, not performed

Caregiver-controlled installation and permission review; manual V8 regression; explicit automatic acquisition/Stop; screen-off and reconnect behavior; process/boot recovery; local buffer and token/endpoint handling; synthetic HTTPS integration; PostgreSQL execution; dashboard rendering and accessibility. No deployment, phone activation, service launch, real-data transmission or security change occurred in this recovery pass. Prior browser IPC access-denied and PostgreSQL/tooling limitations remain. Working APKs and SDK ZIPs were preserved.
