# Independent Mom Health help preview

This is a separate native help-only application (org.momhealth.help), not an update to the watch app. It has zero permissions, no BLE code, no measurement actions, no network, no background work and no storage. Do not confuse it with the separately tested Mom Health gateway. No vendor content or assets were copied. Built with the already installed Android/JDK tools and existing local debug signing key, with no dependency installation.

APK: build/Mom-Health-Help-Preview.apk. Verification: build/verification.json. Help wording: src/org/momhealth/help/HelpContent.java. Native layout: HelpActivity.java. Source uses scalable text, accessibility headings, scrolling and system insets, with no fixed card heights. Actual rendering, large-font/TalkBack checks have not been performed; no emulator authorization was retried.

Independent user acceptance: allow 5-10 minutes after separately approving installation of this preview. Confirm its Help Preview label; read the seven sections; check text clipping at your preferred font scale, scroll reachability, contrast and heading navigation. Compare the named controls with Mom Health 0.5.0. Opening or scrolling the guide must not scan, connect, measure, request permissions or change the watch app. Review the distinction between recent/stale/ended and the limits on accuracy, LEDs, storage and background operation. Keep any integration into the main app pending acceptance. No hardware measurement is required to test this preview.

A source-only wording review can be done without installing anything. The guide is not copied to Downloads or merged into the release candidate by this task.
