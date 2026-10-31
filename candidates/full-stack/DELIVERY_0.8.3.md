# Mom Health 0.8.3 candidate

This separate development candidate improves upload-status reporting. A reading is considered committed only after an accepted or duplicate acknowledgment. Rejected or unacknowledged readings remain pending; if a separate status update fails, the app retains the incomplete-upload explanation, and a later successful retry clears the stale explanation. This is a local candidate and does not establish that a real phone can reach a server.

- APK: `build/0.8.3-candidate/Mom-Health-0.8.3-candidate-V1-V8.apk`
- SHA-256: `089e369ef366ac325000c21b49c02d879f7f195171c0060c964ae1c8634cec86`
- Package `org.momhealth.gateway.candidate`, version code 17, version `0.8.3-candidate`; minimum Android 31, target Android 35.
- Debuggable and signed with the same local development certificate as 0.8.1; not a production release.

Full Android source compilation passed with the installed SDK. Thirteen portable synthetic regression commands passed, including 224 automation-policy assertions. Ten synthetic acknowledgment checks passed. Details are in `build/0.8.3-candidate/verification-298f4da7ef1a445f87d352ea524e4db2/results.json`. These do not execute Android storage, phone networking or BLE.

APK v3 signature and alignment verification passed. Its ten declared permissions match 0.8.1. The previous 0.8.1, 0.8.0 and 0.7.0 hashes remain unchanged. A 0.8.2 build was created before the last source correction; it is preserved as an intermediate and should not be delivered for testing.

No device install, activation or health-data upload occurred. Pixel, Fold and S25 Ultra operation of 0.8.3, automatic behavior, phone-to-server HTTPS, hosted caregiver access and heart-rate accuracy remain unverified. The hosted synthetic-only backend is not a destination for real watch observations.
