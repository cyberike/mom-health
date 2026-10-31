package org.momhealth.gateway;

/** Presentation only. Never changes consent, timing, or watch operation. */
final class CaregiverStatusDisplay {
    static String budget(long delayMs, boolean measuring, boolean attention) {
        if(delayMs <= 0 || measuring || attention) return "";
        long minutes = 1 + (delayMs - 1) / 60000;
        return "Next request no sooner than about " + minutes + " minute" + (minutes == 1 ? "" : "s")
                + " (saved schedule or hourly limit). Android or connection conditions may delay it further.";
    }
    static String reading(Integer bpm, String localTime) {
        if(bpm == null || localTime == null) return "No automatic reading saved in this service session yet.";
        return "Last automatic reading saved: " + bpm + " bpm at " + localTime
                + ". Last received value, not a live measurement; accuracy unverified.";
    }
    static String upload(boolean enabled, boolean available, boolean bootPaused) {
        if(!enabled) return "Automatic upload is off.";
        if(bootPaused) return "Service upload is paused after reboot; separately configured scheduled upload may run later.";
        return available ? "Automatic upload is enabled." : "Automatic upload is paused: credential unavailable.";
    }
}
