# Mom Health 0.8.1 candidate

This separate development candidate corrects upload status reporting: an HTTP success is reported as a successful batch only when every requested sample receives an explicit accepted or duplicate acknowledgment. Rejected or unacknowledged items remain pending. It does not activate upload or establish that a real phone can reach a server.

- APK: `build/0.8.1-candidate/Mom-Health-0.8.1-candidate-V1-V8.apk`
- SHA-256: `3295f4f1062052a19eb77fb856d9a6920f80b46b9d766cd47ee945fc01f03f98`
- Package `org.momhealth.gateway.candidate`, version code 15, version `0.8.1-candidate`; minimum Android 31, target Android 35.
- The APK uses the same local development signing certificate as 0.8.0 and is debuggable; it is not a production release.

The Android source compiled with the installed SDK. Six synthetic acknowledgment checks passed, including a mixed rejected batch that must not commit either item. Thirteen portable regression commands also passed; their detailed output is at `build/0.8.1-candidate/portable-tests-460dbfd099864a4480e9b8d50a50f2ba/results.json`. Five local backend wire-shape integration tests passed separately using fictional data. These do not execute Android storage or phone networking.

APK signature verification passed using signing scheme v3, alignment verification passed, and the ten declared permissions match 0.8.0. The 0.8.0 APK remained SHA-256 `f6cc7f2113f3063b391ea17ed707625d0155323cfeb293b0bc0ec6d3b4d91aa5`; the earlier 0.7.0 APK was also checked before building and preserved.

No device install, activation or health-data upload occurred. Pixel/Fold/S25 Ultra operation of this candidate, automatic behavior, phone-to-server HTTPS, hosted caregiver access and heart-rate accuracy remain unverified. The hosted synthetic-only backend deliberately rejects actual phone health data and status.
