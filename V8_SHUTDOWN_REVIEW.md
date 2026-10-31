# Finalization update: 0.2.2

User at 15:43:53 UTC (Sentinel_83bcef0b1184819193b907a1a36d0408) confirms LEDs turned off once the device was removed from the wrist. Touch-triggered lighting remains possible; command-driven shutdown is not independently established. Parent authorized local finalization, build and Downloads copy, with no installation/device operation.

Implemented the demo-aligned V8 stop duration30/open=false, preserving V1 duration0. Parser accepts 25/32-byte activity frames, reads HR byte21 only, and requires MTU >=35. Other lengths/fields remain excluded. No reordering, retries, acquisition extension or new command type. Historical findings below describe the evidence as it arrived; their withheld-build status is superseded by this finalization.

# V8 0.2.1 hardware failure review - 2026-10-21

Status: physical shutdown remains unverified. The user subsequently clarified that the sample has no button and its light appears to activate on sensor touch. Continued LEDs therefore do not prove failure of app-requested HR stop; autonomous contact detection is possible but unverified. Parent requested an untouched, off-wrist, sensors-up observation for 30-60 seconds, with no new app test. No device operations performed here. No power control or firmware setting is assumed.

## Evidence received

Parent directly reviewed Library screenshot libfile_7b6954686c588191b8db30363581290b: opcode 09 notifications of length 32 approximately once per second inside the confirmed session. Our exact-25-byte decoder rejected all of them and hid byte 21, so no BPM can be inferred. At 09:38:42.574 deadline, HR stop write confirmed at 42.690, reporting-disable write at 42.795. One 32-byte notification arrived at 42.836 after cleanup and was excluded. One late frame cannot establish persistent reporting; it can already have been queued. User initially reported that green light did not turn off, then clarified at 15:42:16 UTC (Sentinel_b6cc4b43404481919ea6a6dd1325d179): there is no button and the light appears to activate when touching the sensors. These observations do not distinguish app-started measurement from autonomous touch behavior. GATT completion is not physical shutdown.

## Supported parser correction, local only

Android SDK root: ../sdk_inspection/V8 SDK/V8 SDK/V8 Android 2436_v8_version2.0(1)/v8test/.

blesdk_2301/src/main/java/com/jstyle/blesdkv8/Util/BleSDK.java:66-90 dispatches opcode 09 directly to getActivityData with no exact-length restriction. other/ResolveUtil.java:207-256 reads fixed prefix fields through index 24; line 235 reads HR at index 21. It ignores any trailing bytes. Thus 25 is a minimum prefix size, not a documented exact packet length. The meaning of bytes 25-31 is not established. No new checksum rule or field is inferred.

Original V8Adapter now accepts only the prefix-compatible 25-byte form and screenshot-observed 32-byte form, reads only HR byte 21, and ignores the suffix. All other lengths remain excluded. Diagnostics display length and byte 21 only, with unrelated metrics omitted. Minimum ATT MTU increased to 35 for the observed 32-byte notification. The 30-second window, session isolation and cleanup logic remain unchanged. Synthetic checks passed: 1114 protocol, 3976 V8, 253 session = 5343. Session tests now exercise 32-byte candidates. No actual payload was supplied; synthetic nonzero HR does not demonstrate device measurement.

## Stop mismatch and limits

Current V8 stop command: 28 02 00 00 00 00 00 00 00 00 00 00 00 00 00 2A.

iOS demo ../sdk_inspection/V8 SDK/V8 SDK/V8 IOS/V8 IOS/Ble SDK Demo/Ble SDK Demo/realtimeStep.m:363-378 starts AND stops with measurementTime:30, changing open:YES to open:NO. Ble SDK Demo/BleSDK/BleSDK_V8.h:207-214 says minimum measurementTime is 30 and open:NO closes measurement. Android builder BleSDK.java:548-572 writes the supplied duration at bytes 4-5 even when open=false; the Java demo RealTimeStepCountingActivity.java:87-98 passes the entered duration independently of its open toggle.

Therefore a demo-aligned V8 stop candidate is 28 02 00 00 1E 00 00 00 00 00 00 00 00 00 00 48 (open=false, duration30). This has NOT been implemented/sent here. Whether firmware ignores/requires duration for stop is unknown. Previous tests with duration0 reportedly stopped LEDs before reporting was enabled, so this mismatch is not proof of causation.

realtimeStep.m:320-335 and :363-378 expose independent reporting and HR toggles, without prescribed teardown order or waiting interval. Android activity likewise exposes separate controls. Current app queues HR stop then reporting disable after write callback; no evidence supports silently reversing order, extending acquisition, adding retries or new commands.

Android BleSDK.java:205-206 and :252-265 labels stop responses according to a local StartDeviceMeasurementWithType flag plus packet subtype. This is not an independent sensor-off measurement. The app must continue to describe write confirmations only. No definitive hardware shutdown-status query was established in the inspected sequence.

## Next step

Await the parent-led off-wrist, sensors-up, untouched 30-60 second observation; do not invent a power button or ask for another app measurement. Separately review the demo-aligned stop-duration correction before any new test. That source mismatch remains real, but neither touch behavior nor continued LEDs establishes that changing duration resolves it. Do not redistribute a parser-only update as a shutdown fix. Existing APKs and SDKs are preserved; no 0.2.2 APK has been built or copied to Downloads. build.py is staged for version 0.2.2; test evidence is build/test-0.2.2-log.json.
