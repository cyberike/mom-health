# Private synthetic test, October 28, 2026

One user-approved finite private test was attempted. No public domain, TCP proxy, SSH key, real health data, phone action or persistent test credentials were created.

- Workspace: `cyberike's Projects` (`083a7465-9578-4195-ada2-991ce428fc0b`).
- Project: `mom-health-synthetic-test` (`43592329-80af-441d-8bb3-be63f554bec5`).
- Environment: `synthetic-test` (`5340c8d6-d1ba-4484-b5da-198a89f801a3`).
- Service: `private-selftest` (`7a58317d-2bdb-44c6-b5e6-493dfaee9139`).
- Volume: `synthetic-test-data` (`caaf5fd0-eecd-4f91-9b4e-fccf9403f603`), 500 MB, /data, retained.
- Deployment: `6aadcc00-7dbc-49f2-8cc2-ca978db17e81`, snapshot `da61c370-338f-4397-a185-da3c4710b44e`.
- Reviewed upload manifest SHA-256: `120e8f489d9ba5dc247218432c424ddd9d3bf1f1ba4eb8005509fa6403fa0dac`; source tree in `build/railway-private-selftest-upload-20261028`. This is a manifest hash, not the deployed image hash. All 20 staged source files were subsequently checked against this manifest and matched. Railway build output separately reported container image digest `sha256:9541e3d2e08e36a89f0812632926c1ee27f59522cdd0125b0ce5457ca86f7073`; this is a build-log report, not independent attestation of the deployed filesystem.

Before execution, Railway read-back confirmed one replica, CPU limit 0.2 vCPU, memory limit 500,000,000 bytes, restart policy NEVER, zero overlap, the intended start command, no domains or TCP proxies, and a 500 MB mounted volume. Four nonsecret environment variables selected self-test and disallowed real data. No persistent credentials were necessary: the test uses fictional in-process fixtures.

Railway build completed and the container started. At 06:51:49 UTC its log reported `SUPERVISOR FAIL: test did not complete successfully; inspect privately before retry`; deployment status became CRASHED. The supervisor intentionally suppresses the child error, so the underlying cause is unknown; a mount/ownership failure is only one possibility, not an established diagnosis. Do not rerun or overwrite the volume database without inspecting state.

The requested immediate stop command `railway down` returned `No deployments found`; read-back continued to show CRASHED with restart policy NEVER. Metrics returned current CPU 0, memory 0 and network TX 0; they are sampled telemetry, not an instantaneous billing guarantee. The deployment is not marked REMOVED. No extra retry or redeployment was made. The retained volume may continue to incur storage charges.

Immediately after the run Railway CLI project usage reported approximately $0.0000000594 resource usage, exclusively volume, with 0 CPU/memory/egress reported at that time. Usage data may lag and is not a final bill. The capacity-based illustrative maximum for 0.5 GB of retained storage is about $0.075/month at Railway's published rate, excluding tax and subject to current pricing/billing terms. No workspace-wide cost limits or unrelated resources were changed.

No result establishes real TLS, proxy isolation, browser authentication, phone upload, device reliability, or unattended operation. This is a failed synthetic infrastructure test, not a delivered product.
