# Private synthetic Railway validation: October 29, 2026

Two bounded runs used the existing `mom-health-synthetic-test` project (`43592329-80af-441d-8bb3-be63f554bec5`), environment `synthetic-test` (`5340c8d6-d1ba-4484-b5da-198a89f801a3`), service `private-selftest` (`7a58317d-2bdb-44c6-b5e6-493dfaee9139`) and retained 500 MB volume (`caaf5fd0-eecd-4f91-9b4e-fccf9403f603`) mounted at `/data`. The service configuration had one replica, 0.2 vCPU, 500,000,000-byte memory limit, restart policy `NEVER` and zero deployment overlap. No public domain or phone operation was involved.

## Read-only preflight

Deployment `94ef35c5-5a7a-49bd-b4e3-6984a73dd45a`, snapshot `735c8fa8-c4ba-41a1-bd7e-e62af3b93ed4`, logged at 04:43:27 UTC:

`PREFLIGHT PASS: ACCESS_ACL=ABSENT CHILD=ABSENT DEFAULT_ACL=ABSENT LEGACY_DB=ABSENT MOUNT=YES MOUNTINFO=YES OTHER_WRITABLE=NO OWNER=YES`

The inspection did not open a database or write to the volume. These labels record the observed mount, ownership, mode and POSIX ACL-xattr checks at that time; absent POSIX ACL xattrs do not establish the absence of all other platform access mechanisms. The preflight was stopped with `railway down`; read-back showed deployment `REMOVED` and no latest deployment.

## Private storage and API self-test

Deployment `17ee3bb7-c624-4356-9bd8-5d50c431239c`, snapshot `cf21d1ca-8f2e-44b5-b61c-d9e7bf031895`, logged at 04:46:12 UTC: `SUPERVISOR PASS: synthetic self-test exited successfully; no ingress or TLS claim`.

The supervisor suppresses child output and reports success only after the self-test exits successfully. The staged self-test contains **12 in-process synthetic checks**: role-based access to the dashboard and API, rejection of unauthorized or wrong-role access, rejection of an unknown route, synthetic batch acceptance, idempotent retry, sample retrieval, rejection of a non-synthetic sample and confirmation that it was not added. It also requires the test database file to exist. The test uses fictional in-process credentials and data; it does not open a network listener or establish persistent credentials. The successful test creates synthetic data in the private child directory on the retained volume; all Linux negative cases were not tested on Railway. Do not overwrite or delete the resulting files without inspecting them.

The 20-file local source manifest, `build/railway-private-selftest-20261029-a-manifest.json`, has SHA-256 `4ad0146dc3445ff90f14089fe296370479c59e104b2a6498eac9ab302df68226`. Source and staged file hashes matched before upload. This is a local manifest hash, not independent attestation of the deployed filesystem.

After the test, `railway down` exited 0; read-back showed the deployment `REMOVED`, no latest deployment and sampled current CPU and memory at zero. The volume remains attached and may accrue storage charges. Immediately before these runs, reported project usage for the current billing period was approximately $0.00014612886; that figure excludes any delayed reporting of the runs and is not a final bill.

## Read-only restart durability check

Deployment `4dc13453-7c76-4a95-8152-2bec3aff441c`, snapshot `4b5da646-5c69-4794-923f-2454ad340ff1`, logged `DURABILITY PASS` at 04:51:30 UTC. The probe reopened the retained SQLite database read-only with immutable mode and ran `quick_check`. Aggregate checks found exactly one synthetic sample, two batch receipts and zero gateway-status entries. No database contents were logged; the probe detected no WAL or journal files or unsafe permissions. The four-file local source manifest has SHA-256 `0844b50f9150e80f7740bfab6b20f33b32f171f4daf63ff7c03d053aafb4a080`.

`railway down` exited 0. Read-back showed the deployment `REMOVED`, no latest deployment, sampled current CPU and memory at zero, and the volume retained. The last reported project usage before the durability run was approximately $0.000146799; billing may lag and the volume may continue to accrue charges. This check demonstrates read-only recovery after one stop and restart, not resilience to power failure, backups or other failure scenarios.

## Limits

This is a private synthetic infrastructure validation, not product delivery. It does not establish public HTTPS or proxy isolation, real caregiver authentication, phone-to-server upload, real health-data processing, device accuracy, unattended collection, physical shutdown, durability across arbitrary failures, backups or S25 Ultra acceptance. The storage checks do not prove protection against all non-POSIX ACLs, privileged or same-service-user path changes, or pathname replacement before SQLite reopens the database. Existing local negative checks use mocked POSIX calls and do not prove those negative cases on Railway. No real health data was sent.
