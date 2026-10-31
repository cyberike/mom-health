# Current implementation: 0.2.1

The documented reporting prerequisite below is now implemented in original code. V8 enables opcode 09 with temperature disabled before manual HR, requires negotiated MTU >=28, and uses only exactly 25-byte opcode 09 packets for HR byte 21. Opcode 28 is treated as command status even when its payload is nonzero. Stop/timeout/exit runs ordered stop then reporting disable with bounded, visible failure handling. No device commands were sent during development. See README.md for transport failure limitations and build evidence. The historical 0.2.0 analysis below is retained to distinguish the initial hypothesis from the later decisive SDK evidence.

# V8 investigation and 0.2.0 changes

The physical sample is V8 according to the user and has no display. The earlier V1 confirmation was a UI selection, not a hardware identification. The parent inspected screenshots; this subthread received their packet transcription, not a direct device capture.

Screenshot references: libfile_d901753c20c88191b7e363b740e1682d (07:18:54 UTC upload) and close-up libfile_f03bdeb0931081919d2350d08e2ee5d0 (06:14:57 UTC upload). Transcription is subject to visual error:

```
Firmware RX 27 00 00 08 08 26 06 04 01 00 19 06 06 00 09 96
Battery RX  13 64 00 42 58 42 58 64 64 64 00 00 00 00 00 D7
00:14:01.700 TX 28 02 01 00 1E 00 00 00 00 00 00 00 00 00 00 49
00:14:01.758 RX 28 02 00 00 00 00 00 00 00 00 00 00 00 00 00 2A
00:14:31.789 TX 28 02 00 00 00 00 00 00 00 00 00 00 00 00 00 2A
00:14:32.534 RX 28 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 28
```

The manual HR reply contains zero at byte 2. It is not 42 bpm: 2A is the final checksum. The initial reply is consistent with acknowledgment/no-result behavior, but the SDK does not establish a precise zero-status meaning. User-observed LEDs support control, not an HR measurement. Battery 100% and firmware 0.0.8.8 match the parser layouts.

## Source evidence

V8 source root: `../sdk_inspection/V8 SDK/V8 SDK/V8 Android 2436_v8_version2.0(1)/v8test/`.

- `blesdk_2301/src/main/java/com/jstyle/blesdkv8/Util/BleSDK.java:548`: 16-byte HR request uses opcode 28, type 2, enabled byte 1, little-endian duration at 4-5 and additive checksum. A 30-second request matches our previous V1 command exactly.
- Same file at 223: manual HR is byte 2; V8 callback accesses through byte 7. V1 accesses through byte 9. A shorter V8 frame could be rejected by V1, but the supplied zero-HR frame is 16 bytes, so that is not its cause.
- `other/ResolveUtil.java:338,380`: battery percentage byte 1, firmware hex bytes 1-4. Additional V8 battery fields exist but are not needed for this app.
- `app/src/main/java/com/jstyle/test2025/ble/BleService.java:46,731,930`: same FFF0/FFF6/FFF7 service/write/notify identifiers and CCC notification setup. Notifications are forwarded as received. No distinct manual-HR characteristic was identified.
- `other/ResolveUtil.java:207`: separate opcode 09 activity telemetry contains HR at byte 21 and fields through byte 24. `BleSDK.java:406` exposes a separate activity toggle. Neither evidence nor the screenshot proves this is required for manual HR. This release does not enable it or promote activity HR to a fresh manual result.

## Implemented

Explicit V8/V1 selection before scanning and physical-model confirmation before connecting. V4 remains unavailable. Separate original V8 decoder; no vendor libraries. Zero/invalid manual HR is NO_RESULT, never a numeric reading. UI supports a screenless sample.

Deadline remains 30 seconds, now anchored to the submitted start command instead of the later write callback. Confirmation cannot extend it. Stop, background exit and stale-GATT/session guards remain. Packet arrival timestamps, lengths, session tokens and window phase are shown on screen, including rejected/late candidates. A queued pre-confirmation packet stays excluded after confirmation. There is no late-result acceptance grace period or automatic activity streaming. Android scheduling and radio failures can delay/prevent Stop delivery; the firmware also receives duration 30. No stronger physical guarantee has been established.

The wire format lacks request IDs or a measurement timestamp. App session isolation prevents queued callbacks being relabeled but cannot prove physiological freshness of a newly delivered device value. All accepted HR remains device-reported/unverified. Sensor acquisition failure, firmware-specific output, and a final result at/after timeout remain hypotheses until another user-led test captures them.

Synthetic fixtures and the parent's screenshot-transcribed regression cases do not constitute a new hardware test. No phone installation, permission approval, BLE operation or sensor command was performed during this build stage.


## Follow-up: explicit streaming prerequisite found (Pixel 10 test)

The parent's direct review of screenshot libfile_bb0757d3f64881918f68a74a00965071 shows 0.2.0 on Pixel 10: firmware 0.0.8.8, battery 95%, a confirmed 30-second manual HR request, an in-window zero-HR reply, and a stop reply. No intervening or late HR is visible. This does not support claiming timer exclusion as the explanation.

New decisive evidence: `../sdk_inspection/V8 SDK/V8 SDK/V8 IOS/V8 IOS/Ble SDK Demo/Ble SDK Demo/infoView.m:32` contains the demo's displayed help text:

> 注意 : 只有在实时计步处于开启状态下，手动测量才会有数据返回

Translation: manual measurement returns data only while real-time step reporting is enabled. The same help says the activity stream's heartRate is valid only while manual HR measurement is enabled. It describes the manual command callback's data dictionary as nil. This supersedes the earlier limited conclusion that activity streaming was merely an alternative with no documented prerequisite. The initial Android-only reading missed this explicit iOS demo help.

The companion `realtimeStep.m:285-304` displays HR from RealTimeStep_V8 callbacks; lines 320-335 independently enable/disable real-time reporting; lines 363-378 start/stop manual HR for 30 seconds. In the Android root above, `activity/RealTimeStepCountingActivity.java:93-111` offers the same two separate controls and lines 123-140 display HR from activity callbacks. The Kotlin counterpart `ui/RealTimeStepCountingActivity.kt:73-97` updates its HR display only from RealTimeStep callbacks.

Android `BleSDK.java:406-412` documents the original command builder: 16 bytes, opcode 09, byte 1 = enable flag, byte 2 = temperature flag, additive checksum. With temperature disabled, enable is `09 01 00 00 00 00 00 00 00 00 00 00 00 00 00 0A`; disable is `09 00 00 00 00 00 00 00 00 00 00 00 00 00 00 09`. These are proposed packet facts only; none was sent. `other/ResolveUtil.java:207-256` reads activity HR from byte 21, and uses fields through byte 24. A full parser-compatible activity payload therefore needs at least 25 bytes. Do not guess fragmentation or treat a short prefix as a complete frame.

Connection inspection: Java `ble/BleService.java:646-682` subscribes notifications, and `:705-715` requests high connection priority and advances the queue after CCC success. Kotlin `ble/BleService.kt:320-378` similarly subscribes and advances the queue. No automatic time/profile/measurement-setting prerequisite was identified in those connection handlers; time, profile and automatic measurement writes are separate UI actions. V8 header `BleSDK_V8.h:207-214` specifies minimum manual measurement duration 30 seconds. Longer acquisition or changing measurement type is not justified by this evidence.

Smallest justified next change/test proposal (not implemented in this investigation): within a user-approved V8 HR session, establish complete-notification transport (negotiate/record ATT MTU sufficient for a 25-byte payload, at least 28, or fail clearly if the device cannot supply complete frames); enable real-time activity reporting with temperature disabled; then submit the existing 30-second HR start. Only accept the byte-21 HR candidate while both the stream and confirmed manual HR session are active. Preserve raw payload, source opcode and receive time, label it device-reported/unverified, and ignore unrelated fields. On Stop, timeout, background exit or failure, invalidate the session immediately and attempt both manual-HR stop and activity-disable, with bounded setup/cleanup deadlines and honest delivery status. No auto-reconnect, permanent automatic measurement setting, new sensor type or longer acquisition is needed.

The final two-command teardown and its fault handling must be implemented/tested before a new device trial; simply adding opcode 09 to the existing four-command allowlist is insufficient. The evidence strongly supports missing streaming as an app integration gap, but success on this exact firmware remains unverified. Sensor contact/acquisition can still fail after correcting transport. The current 0.2.0 APK is unchanged and cannot perform this new sequence.
