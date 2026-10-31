package org.momhealth.help;
public final class HelpContent {
 private static final String[][] SECTIONS={
 {"Before you begin", "This is a help preview, not the watch app. It cannot connect, measure or save readings. Use the separate Mom Health app for the steps below. Keep your charged watch nearby. Readings are watch-reported; accuracy has not been independently verified."},
 {"1. Connect your watch", "In Mom Health, choose the physical model: V8 or V1. Tap Find my watch, select only your known watch, and confirm the model. A Bluetooth name alone does not establish the model. Connection does not begin a measurement. Read battery / firmware when connected."},
 {"2. Measure when you choose", "Tap Measure heart rate - 30 seconds. Keep Mom Health open and wear the watch as you did during the tested setup. The screen stays awake during preparation and measurement. Tap Stop measurement to end early. Otherwise the request ends after its 30-second acquisition limit. No reading is guaranteed."},
 {"3. Understand the reading", "No reading yet means no accepted BPM is available. Current session - recently received means a new watch-reported value arrived during this request; it does not prove accuracy. Stale means no new accepted reading has arrived for five seconds. Measurement ended means the displayed number is only the last reading. Check Last received before interpreting a number."},
 {"4. Return after screen sleep", "Leaving Mom Health or letting the screen sleep disconnects it by design. Reopen it, tap Reconnect selected watch, and confirm your physical watch. Reconnect does not start measurement. Tap Measure separately when ready. The selection is kept only in the current app activity; after recreation or restart, choose the model and find the watch again."},
 {"5. If a step does not work", "If reconnect fails, use Find my watch for a new scan. No automatic retry runs. A zero or missing result is not a BPM reading. Open Show connection details and log to inspect the reason. If cleanup is unconfirmed, do not assume the sensor stopped. LEDs alone do not establish measurement success or command-driven shutdown."},
 {"What this prototype does not do", "No background monitoring, health alerts, medical diagnosis or independently verified accuracy. No history sync, cloud upload or saved health history. The current app keeps readings and diagnostic text only in memory. V4 is not enabled. This preview adds none of those capabilities."}
 };
 public static int count(){return SECTIONS.length;}
 public static String[] section(int index){return SECTIONS[index].clone();}
 private HelpContent(){}
}
