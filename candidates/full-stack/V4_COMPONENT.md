# Inactive V4 / 2501 adapter

`src/org/momhealth/gateway/V4Adapter.java` implements original narrow protocol code from local source evidence. It is not selected by the UI, does not send commands, and does not change V1/V8 behavior. V4 real measurements remain capability-gated unavailable in storage/backend policy. No vendor code, binary, script, or installer was executed or incorporated. Hardware behavior and SDK redistribution/license permission remain unverified; this implementation contains no vendor library.

## Source evidence

All paths below are relative to the exact inspected source root:

`C:\Users\serge\Documents\Codex\2026-10-20\task\sdk_inspection\2501 (v4) SDK\2501 (v4)\V4 SDK(Android)\2501test`

The SDK utility root below is `blesdk_2208/src/main/java/com/jstyle/blesdk2501/` (the folder is named 2208 despite the package being 2501).

| Fact | Source and lines |
| --- | --- |
| FFF0 service, FFF6 write, FFF7 notify UUIDs | `app/src/main/java/com/jstyle/test2501newz/ble/BleService.java:49–51` |
| Activity 09, battery 13, firmware 27, manual measurement 28 opcodes | SDK root `constant/DeviceConst.java:15,18,20,77` |
| Battery/firmware requests are 16 bytes with opcode and additive checksum | SDK root `Util/BleSDK.java:1175–1179,1289–1293` |
| Manual measurement bytes: subtype at 1, open at 2, little-endian duration at 4–5 only when open | SDK root `Util/BleSDK.java:1522–1534` |
| Duration clamp 30–65535 seconds | SDK root `Util/BleSDK.java:1511–1517,1528–1531` |
| HR subtype is 2 | `app/src/main/java/com/jstyle/test2501newz/activity/MeasurementActivity.java:60–62`; SDK root `Util/BleSDK.java:521–532` |
| Manual demo sends only measurement command from its button, not a reporting enable prerequisite | `app/src/main/java/com/jstyle/test2501newz/activity/MeasurementActivity.java:75–79` |
| Activity enable byte1, temperature enable byte2, 16-byte request | SDK root `Util/BleSDK.java:841–847` |
| Checksum is sum of preceding bytes modulo 256 | SDK root `Util/BleSDK.java:1949–1954` |
| Manual HR callback reads HR byte2 and other fields through byte7 | SDK root `Util/BleSDK.java:504–532` |
| SDK stop callback classification depends on a host-side global open flag, not independent sensor-stop proof | SDK root `Util/BleSDK.java:505,548–560` |
| Activity dispatch and HR offset21; full SDK parser reads through byte23 | SDK root `Util/BleSDK.java:257–258`; `Util/ResolveUtil.java:173–220` |
| Battery byte1; firmware four hex components bytes1–4 | SDK root `Util/ResolveUtil.java:292–300,330–341` |

## Implemented command and parser scope

Only six exact commands are allowlisted: battery, firmware, 30-second manual HR start, manual HR stop, reporting enable with temperature disabled, reporting disable. Any byte mutation is rejected. Start ends with checksum 49 hex; stop has zero duration and ends 2A hex. V4 source explicitly zeroes duration on stop, so V8's nonzero stop-duration behavior is not copied.

`needsReporting()` remains false because the V4 manual demo provides no mandatory reporting prerequisite. Optional reporting methods are provided without activating them. A future reporting integration needs complete notification delivery and at least MTU 27 to carry the 24-byte SDK-consumed prefix; that lower bound is not proof of the actual firmware's full frame size. It must also provide bounded cleanup, session verification and a separately authorized hardware test.

Decode prefix bounds: battery at least 2 bytes, firmware at least 5, manual HR at least 8 with subtype2, activity at least 24. All inputs are capped at 512 bytes as a local defensive bound. The SDK does not declare an exact notification frame length in these paths and ignores trailing bytes. Therefore these are supported *parser prefix lengths*, not physically observed V4 frame lengths. Extra bytes are uninterpreted; no V8-specific 32-byte firmware framing is asserted. The SDK paths do not validate inbound checksums; this adapter likewise cannot claim integrity validation. It never treats a trailing byte as a measurement.

Only HR, battery and firmware are returned. The HR range 20–250 is an engineering rejection filter, not clinical validity. Zero/out-of-range HR becomes NO_RESULT. Other measurement subtypes, short packets and unknown opcodes are UNKNOWN. Start/stop command echoes cannot produce an HR. Report timestamps, freshness, session identity and physical shutdown are not available from this parser; the controller must enforce the confirmed active session and deadline. No history, ECG, BP, glucose, temperature, deletion, OTA, settings or automatic measurement commands are allowed.

## Build and deferred acceptance

`tests/V4AdapterChecks.java` contains synthetic check source for exact commands/checksum, mutated command rejection, UUIDs, subtype rejection, bounds, partial frames, extension prefixes, invalid HR, and command echoes. It has not been run. Compilation checks syntax and type compatibility only.

Hardware acceptance remains pending: confirm actual 2501 service/characteristics, metadata frame sizes, manual HR route, notification completeness/MTU, fresh readings during a bounded request, stop behavior, late callback exclusion, and whether optional reporting is necessary on physical firmware. No phone/watch operation or app installation was performed.
