# Caregiver service candidate

This is compiled candidate code, not an activated or hardware-validated service.

CaregiverAutomationActivity collects explicit acquisition, recording, upload and restore choices. CaregiverAutomationStore retains configuration and acquisition budget. CaregiverService owns bounded BLE requests, a visible foreground notification and Stop action. WatchConnectionLease excludes simultaneous manual ownership. CaregiverUploadJobService provides constrained, separately consented uploads. CaregiverCredentialVault contains optional Android Keystore encryption code; no credential or key was created during this build. GatewayStatusClient sends last-reported technical status only when upload is enabled.

The recovered interrupted edit was absent. It is now completed: durable cadence defers only acquisition through AutomationEngine.deferAcquisitionUntil; it does not set power availability false and thereby suppress independent uploads. The CPU wake window is bounded at 60 seconds to cover preparation, the unchanged 30-second acquisition maximum, and cleanup. This does not lengthen sensor acquisition. Unconfirmed cleanup requires caregiver review. GATT confirmation does not independently prove physical shutdown.

Default operation remains disabled. Installing this package was not performed. No phone permissions, Bluetooth commands, services, network uploads or boot recovery were activated during verification.

Portable verification covers configuration validation, upload ownership and deterministic engine policy. Android service lifecycle, SQLite execution, Keystore, JobScheduler, notification permissions, power-management behavior and physical shutdown remain unverified on a phone. Test these with caregiver participation before relying on hands-off operation.
