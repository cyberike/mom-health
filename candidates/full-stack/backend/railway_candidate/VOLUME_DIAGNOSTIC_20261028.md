# Read-only volume diagnostic: October 28, 2026

One additional private diagnostic was authorized and run. It performed no DB open or writes, no real-data access, no public endpoint, and no phone operations. No further run is authorized by this record.

- Project `43592329-80af-441d-8bb3-be63f554bec5`, environment `5340c8d6-d1ba-4484-b5da-198a89f801a3`, service `7a58317d-2bdb-44c6-b5e6-493dfaee9139`, volume `caaf5fd0-eecd-4f91-9b4e-fccf9403f603`.
- Diagnostic deployment `38f8792c-6806-40b0-9518-f2e6343da083`, snapshot `792aa4ee-a4a2-49e0-94cf-0f18127bd91c`.
- Minimal nine-file upload-tree manifest SHA-256: `d47fe5e8cffd181df26da8c2c20155a4258d07c0c9b26e691c8a8d8707d67b11`. This identifies the local source manifest, not a provider-attested deployed image.
- Before execution, service read-back confirmed 0.2 vCPU, 500,000,000 bytes memory, one replica, restart NEVER, zero overlap, 500 MB volume at /data, no public domains and no TCP proxies.

At 07:37:38 UTC the deployment log reported: `INSPECTION COMPLETE: ISMOUNT=YES MOUNTINFO=YES OWNER_MATCH=YES PRIVATE_MODE=NO DATABASE=ABSENT IMPORTS=OK`.

The directory was recognized as a mount by both mechanisms and owned by the service user, but had some group or other permission bit set; the test did not report its exact mode. The specified DB path was absent. Imports of FastAPI TestClient and the gateway succeeded; this does not establish that database/API tests pass. Current permissions would fail the original candidate's private-mode check, so this is a strong possible explanation for the previous failure, but the original run logged no category and historical permissions were not independently measured. Do not claim the cause is proven.

Immediately after the diagnostic, `railway down` exited 0. Read-back reported both deployment records REMOVED, latestDeployment null, sampled current CPU and memory zero, no domains or TCP proxies and the retained volume still present. Retained storage can continue accruing charges; no instant billing cutoff is claimed. Project usage at the post-run check was $0.00000481792, reported entirely as volume and subject to billing delays and tax. No workspace cost limit or unrelated project was changed.

Local Windows tests: inspector 4 passed, one POSIX-specific test skipped. Independent code review found no direct DB write/open, network call, or credential leak. Platform-level TLS/proxy/access controls, real Android transport, S25 Ultra operation and unattended reliability remain unverified.

## Local follow-up, not deployed

A separate opt-in private-child candidate creates an owner-private 0700 directory inside the mounted volume rather than changing volume-root mode. It first requires the root to be owned by the service user and not group/other writable, uses no-follow directory file descriptors, and keeps atomic no-overwrite DB creation. It does not chmod the parent or delete existing data. Six mocked-POSIX tests, 11 self-test tests and 10 gateway tests passed. Independent review found no direct bypass under a trusted service-user/administrator assumption; ACLs, actual POSIX filesystem behavior and pathname races need real platform verification. See PRIVATE_CHILD_CANDIDATE.md.

The cloud diagnostic's PRIVATE_MODE=NO did not specify the root's write bits, so it does not prove that this conditional candidate will accept the volume. Native POSIX behavior was not tested: WSL distribution enumeration failed with `Wsl/EnumerateDistros/Service/E_ACCESSDENIED`, and was not retried or bypassed. There was no third cloud run.
