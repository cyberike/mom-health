# Manual reconnect update - 0.5.0

Reconnect selected watch requires an explicit tap and physical-model confirmation. The selected BluetoothDevice and label live only in this Activity instance, are cleared by model changes and are not persisted across process/activity recreation. Reconnect is unavailable while backgrounded, scanning, connecting, connected or cleaning up. It performs one existing bounded connection attempt without automatic retries, identity reads or HR start. If a saved address stops working, Find my watch remains available for an explicit rescan. No new pairing, permission or device-setting behavior.

Disconnected status distinguishes deliberate foreground-exit/screen-sleep closure, user Disconnect and an otherwise failed/ended connection. Android onStop does not identify whether sleep or an app switch caused it, so wording deliberately covers both. Existing detailed failure logs remain visible in diagnostics. New selection/reconnect clears the entire reading model, BPM and receipt timestamp, not just the text widget. Measurement still requires its separate button.

5931 synthetic assertions pass (26 new reconnect workflow assertions); Android compile, v3 signature, alignment, existing Bluetooth-only permissions and source/Downloads hashes verified. BLE controller, adapters, bounded acquisition/cleanup, reading state and screen-awake policy are byte-for-byte unchanged from 0.4.2. Source readability review found scrolling vertical layout, scalable text and wrap-content heights; actual device large-font and reconnect checks are pending. No emulator authorization retry or physical device command occurred.

Release: build/Mom-Health-0.5.0-V1-V8.apk; verification: build/verification-0.5.0.json. The verified 0.4.2 fallback and previous releases are preserved.

User-led acceptance on Pixel 10 then Fold 6: connect and measure once; allow normal sleep then reopen; verify explicit exit explanation; reconnect and confirm no automatic HR; deliberately start HR and check new reading/time; exercise Stop and timeout; verify unavailable-watch attempt ends without repeat, and manual rescan remains possible; check cleanup blocks reconnect; verify model/device changes remove prior BPM; check large-font labels/buttons. No accuracy claim follows from these checks.

# Screen-awake update - 0.4.2

The app window uses FLAG_KEEP_SCREEN_ON only while resumed and the existing session reports preparing/starting/active. Preparation retains its existing five-second bound and acquisition its original 30-second bound; neither is extended. Stop/timeout/failed start/transport error changes the session to cleanup or idle and clears the flag immediately. Pause, foreground exit and destruction also clear it, and later callbacks cannot reenable it in the background. No wake lock, keyguard dismissal, global setting, new permission or BLE behavior change. Manual power-button locking is not intercepted. This policy is covered by 22 new synthetic session/lifecycle assertions; actual Android screen-timeout/power-button behavior is untested.

Release: build/Mom-Health-0.4.2-V1-V8.apk; verification: build/verification-0.4.2.json. 5905 synthetic assertions passed. The empty-state and inactive Disconnect polish from 0.4.1 is included. Prior releases and SDKs preserved.

# Dark-card visual update - 0.4.0

Original native Android design based on the parent-viewed reference descriptions: charcoal canvas, rounded cards, white HR numerals, coral HR accents, blue/purple header gradient and teal/blue setup accents. No vendor artwork, logos, promotional claims or extra health features. Current/stale/ended reading labels and the single Measure/Stop control are preserved. Connection and last-read battery share a card; setup and expandable diagnostics follow. Main text uses scalable sp, buttons remain at least 56dp high, and the entire page scrolls. Selected foreground/background contrast pairs tested above 4.5:1; this is not rendered accessibility certification.

Explicit log messages now distinguish User tapped Disconnect from Foreground exit (Activity.onStop), before the existing cleanup call. No lifecycle, reconnect, protocol or cleanup behavior changes. The existing connection-selection text reset now also resets the tested display model to avoid retaining a prior watch's BPM. BLE controller/adapters/window/session engine and ReadingDisplay are byte-for-byte unchanged from 0.3.0. No added permissions or data collection.

Build: `python build.py`. Release: build/Mom-Health-0.4.0-V1-V8.apk. Verification: build/build-0.4.0-log.json and build/verification-0.4.0.json. 5878 synthetic assertions pass. Original prior releases remain available. Actual rendering, font-scale and TalkBack QA are pending: an installed AVD was found, but no emulator launch/install or phone/device operation was performed in this task.

Historical release notes follow; their version-specific paths and outcomes remain historical.

# Foreground usability update - 0.3.0

Large 56sp watch-reported BPM, receipt time, and explicit recent/stale/ended labels. Receipt freshness expires after five seconds without a new accepted packet; this is a UI age threshold, not a clinical rule. Stop/timeout/disconnect immediately marks the reading ended. A new session shows any retained number as previous until a new accepted reading arrives. Changing watch/model clears the old value. A zero/no-result packet never refreshes an old BPM.

One main button starts the existing bounded 30-second request and becomes Stop while preparing/measuring; it is disabled during cleanup. Model selection and known-device confirmation are preserved. Battery is labeled last-read; diagnostics and firmware are behind Show connection details and log. Relevant connection/cleanup messages remain visible outside the collapsed log. Buttons are full-width with a 56dp minimum height, text uses scalable sp, spacing uses dp, and the page scrolls with system-bar insets. Status changes have text labels, not color alone. No real-device/emulator rendering or TalkBack/font-scale QA has been performed.

BLE transport, both adapters, measurement window, and cleanup state machine are byte-for-byte unchanged from verified 0.2.2. There are no extra reads, writes, sensors, permissions, persistence or background collection. The one-second UI refresh only runs while the activity is visible. Clear diagnostic log does not erase or relabel the last measurement.

Build validation: 1114 protocol + 3979 V8 + 253 session + 532 presentation assertions = 5878 synthetic assertions, Android compilation, APK v3 signature, alignment and Bluetooth-only permissions. Existing live hardware observations belong to 0.2.2, not this UI build. Two user-reported sessions (including one after disconnect/reconnect) produced BPM; no independent reference was available and accuracy remains unverified.

# Mom Health 0.3.0 - bounded V8 manual HR integration

The V8 test now enables documented opcode 09 reporting (temperature disabled), confirms the write, then starts the existing 30-second manual HR command. Only complete 25- or 32-byte activity notifications can supply a byte-21 HR candidate, and only after start-write confirmation inside the current session. Manual command replies are status, never BPM. V8 stop now retains duration 30 with open=false, matching the SDK demo; it does not start another acquisition. V1 protocol behavior is preserved; V4 remains unsupported.

V8 connection setup requests ATT MTU 64 and requires a confirmed MTU >=35 before subscribing. Incomplete, oversized, zero or out-of-range HR data is excluded. Firmware formats that fragment or combine frames are not guessed. The app shows setup, acquisition and cleanup states and timestamped diagnostics; unrelated activity fields are omitted from the screen.

Stop, the original 30-second acquisition deadline, and foreground exit invalidate readings immediately, then queue HR stop followed by reporting disable. Rejected/failed writes still permit the other cleanup command to be attempted. Setup is bounded to 5 seconds and teardown to 16 seconds, including an outstanding startup write. A missing GATT callback quarantines the connection because a later callback cannot safely be assigned to a subsequent write. Disconnect, permission loss or timeout can therefore prevent cleanup delivery: the app explicitly reports it as unconfirmed and closes. GATT success does not prove physical shutdown. No automatic reconnect, repeat acquisition or streaming outside this explicit test is initiated. Android process death and radio loss cannot guarantee reporting disable on the device.

Build: `python build.py`. Output: `build/Mom-Health-0.3.0-V1-V8.apk`; evidence: `build/build-0.3.0-log.json` and `build/apk-0.3.0-sha256.txt`. Existing 0.2.2, 0.2.1, 0.2.0 and 0.1.1 APKs are preserved. `--test-only` runs synthetic original-code tests. No vendor SDK binary, build hook, download, installation or device operation is used.

After separate installation/test approval: choose physical V8, connect the known sample, read identity, tap Start HR once. Observe reporting-enable confirmation before HR start, complete activity frames, and any in-window BPM. At Stop/timeout confirm both cleanup writes and physically check LEDs. The latest user test phone is Pixel 10. An independent reference is required; device-reported BPM is not medically validated or proven physiologically fresh. The wire format has no request ID or measurement timestamp, so session isolation excludes stale app callbacks but cannot identify a device-cached value newly delivered in a later session.

Permissions remain BLUETOOTH_SCAN and BLUETOOTH_CONNECT only. No Internet permission, cloud, persistent storage, background service, BP, glucose, ECG, OTA or delete operation. Canonical original source is `src/org/momhealth/gateway`; SDK originals are preserved and vendor redistribution rights remain unresolved. See V8_FINDINGS.md for source evidence and PROTOCOL.md for V1.

The user reports LEDs turned off immediately on removing the sample from the wrist, and appear touch-activated. This does not prove or disprove app-command shutdown. The 32-byte fix and demo-aligned stop require separate hardware validation. See V8_SHUTDOWN_REVIEW.md for the evidence correction.
