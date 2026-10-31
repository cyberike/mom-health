# V1 protocol evidence and unknowns

Evidence source: local `2208A（V1） SDK.zip`, SHA-256 `078a1f6d8f96d110b23510dbb68778150aabd69d37d73db581358995a5cdba9a`.

Under nested `SDK - Android_2208a_v6.zip`, the reference root is `2208aSdk/2208atest/`:

- `app/src/main/java/com/jstyle/test2208/ble/BleService.java:42` declares CCC 2902, service FFF0, write FFF6 and notify FFF7 using the Bluetooth base UUID.
- `blesdk_2208/src/main/java/com/jstyle/blesdk2208a/constant/DeviceConst.java:18` declares battery opcode 0x13; line 20 declares firmware 0x27; line 76 declares measurement 0x28.
- `blesdk_2208/src/main/java/com/jstyle/blesdk2208a/Util/BleSDK.java:1152` builds the 16-byte firmware request; line 1266 builds battery; line 1500 encodes measurement type, on/off and a little-endian duration at bytes 4–5. Line 1928 sums preceding bytes into the last byte.
- `app/src/main/java/com/jstyle/test2208/activity/MeasurementActivity.java:57` selects type 2 for HR. Its line 79 passes 120000; the builder clamps to 65535 while comments describe seconds and incorrectly call 120000 two minutes. The original app here uses 30 and a phone-side 30-second stop. Verify duration on hardware.
- `BleSDK.java:507` maps measurement response type 2 to HR at byte 2 and reads fields through byte 9. We require at least 10 bytes, ignore the other fields, reject start/stop echoes and accept only values 20–250 as engineering candidates. This is not a clinical validation range.
- `blesdk_2208/src/main/java/com/jstyle/blesdk2208a/Util/ResolveUtil.java:295` reads battery from byte 1; line 333 formats firmware bytes 1–4 as hexadecimal components. These layouts are also present in the SDK's other parser copy; packet behavior remains untested.

Exact source paths and original line excerpts are also in `../sdk_inspection/source_evidence.txt`. This app does not link, execute, or copy those classes.

Outbound allowlist is exactly four byte sequences: firmware read, battery read, 30-second HR start, HR stop. There is no arbitrary command interface.

Inbound integrity/checksum behavior is not established by the inspected parser; no unproven inbound checksum rule is imposed. We bound packet length to 20 bytes for this narrow response subset, validate type/fields, and retain raw bytes onscreen. Unsupported fragmentation/batching is ignored, not guessed. Frames have no authenticated model identifier, request ID or reliable measurement timestamp; phone receive time and active-window gating cannot prove a watch value was freshly sampled. Hardware comparison is mandatory.

Writes require the GATT write-with-response property and a successfully enabled CCC. A watch exposing different properties fails closed. No MTU increase is needed for 16-byte commands. GATT write success confirms transport completion, not sensor shutdown or measurement success.
