# Full-brief candidate coverage - build only

**Historical status:** This file records an earlier build-only boundary, not the current authorization or test status. Later work includes synthetic local/private Railway tests, supervised device observations and a separate caregiver-dashboard goal. See `PROGRESS_20261029_FOLLOWUP.md`, `PROGRESS_20261029_SECURITY_REVIEW.md`, `DELIVERY_0.8.3.md` and `PHONE_ACCEPTANCE_0.8.3.md` for newer evidence and remaining acceptance gates.

Authority: original canonical brief received in the initial delegation, plus latest explicit expansion to implement all specified code and the latest BUILD ONLY clarification. No separate full original brief file is present in the workspace; parent thread read returned no turns. Coverage below uses the actual available brief, not invented product sections.

Latest boundary: write/compile/package only. No tests, app runtime, services, installation, deployment, device operation or activation. Existing earlier test records belong to earlier work and do not validate this candidate. Prior to this clarification this phase performed tooling inspection, a denied Docker server-version read, and a candidate source copy; no new app/backend tests or services ran.

| Brief feature | Baseline | Candidate build status | Dependency / exact blocker |
|---|---|---|---|
| One canonical Android BLE gateway independent of consumer cloud | 0.5.0 V8 user-tested | Baseline source copied into isolated package; not installed | Hardware acceptance after joint test authorization |
| Per-model DeviceAdapters V1/V4/V8 | V1 source-only; V8 transport observed; V4 absent | V1/V8 adapters plus inactive V4 adapter written; registry keeps V4 unavailable in UI | Physical V1/V4 compatibility unverified; no invented claims |
| Nullable normalized health data | HR display only | Android and backend contract code written and compiled | Other measurements remain unsupported/unavailable |
| Offline buffering | No persistent buffer | Native SQLite store and bounded pending upload queue written and compiled | Recording must be opt-in; no real data used now |
| Historical retrieval if supported | None | SDK codecs/read builders and bounded inactive transfer planner written; actual BLE history acquisition incomplete | Only documented formats; hardware-unverified commands stay disabled |
| Batch upload | None | Explicit cancellable Android client + transactional backend ingest written and compiled | No configured destination/credentials; real-data activation separate |
| Battery/connection/sync status | Battery/connection working | Sync pending/sent/error UI written and compiled, not run | No automatic reconnect or background sync |
| FastAPI/Python backend | None active | API/validation/repositories written; Python syntax compilation complete | Existing FastAPI/Uvicorn/Pydantic/HTTPX available; not launched |
| PostgreSQL database | None | Schema/migration/driver adapter written, not executed | No installed PostgreSQL driver/server found; Docker API access denied at npipe dockerDesktopLinuxEngine. No retry/install |
| Compare models with identical tests | V8 physical observations only | Common fixture/test sources prepared, not executed | Future approved tests and devices |
| First hardware milestone | V8 HR observed on Pixel/Fold | Preserve tested 0.5.0 and 0.4.2 artifacts | Accuracy remains unverified; S25 Ultra untested |
| Privacy and safe interpretation | No cloud/clinical claims | Separate disabled-by-default controls and honest unavailable states | Public deployment/access/retention decisions remain activation steps |

Ownership: backend_build owns backend/**; android_data_build owns src/.../data/** and data tests; history_adapter_build owns src/.../history/** and history tests; parent owns MainActivity/UI composition, candidate manifest/build, coverage and acceptance documentation. No overlapping file ownership. Candidate build is separate from the working release.

Build-only estimates: baseline/coverage 15-25 minutes; backend/database 90-150; Android data/upload 90-150; history/adapters 60-120; candidate UI/packaging 60-120. Independent sections run concurrently; estimates exclude all runtime/hardware testing now deferred by request.


Completeness audit supersedes earlier general coverage descriptions: see REQUIREMENT_AUDIT.md for every supplied requirement, concrete files, exact specification gaps and unexecuted implementation status. The inactive V4 adapter and history transfer planner are added in 0.6.1-candidate; 0.6.0-candidate artifact/source ZIP remain preserved.

