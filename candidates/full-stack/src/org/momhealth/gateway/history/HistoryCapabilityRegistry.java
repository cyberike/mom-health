package org.momhealth.gateway.history;

/** Source evidence is not hardware validation. No history transport is enabled. */
public final class HistoryCapabilityRegistry {
    private HistoryCapabilityRegistry() {}
    public static final class Capability {
        public final String model;
        public final boolean liveAvailable;
        public final boolean historyAvailable = false;
        public final boolean historyFormatDocumented = true;
        public final String liveEvidence;
        public final String historyReason = "History unavailable: terminal framing, pagination and device time require model-specific hardware validation. No history commands are dispatched.";
        private Capability(String model, boolean live, String evidence) {
            this.model=model; this.liveAvailable=live; this.liveEvidence=evidence;
        }
        public void requireHardwareHistory() {
            throw new UnsupportedOperationException(historyReason);
        }
    }
    public static Capability forModel(String model) {
        if ("V8".equals(model)) return new Capability(model,true,"User-confirmed live HR transport; accuracy unverified.");
        if ("V1".equals(model)) return new Capability(model,true,"Existing source-based live adapter; physical model acceptance pending.");
        if ("V4".equals(model)) return new Capability(model,false,"Source evidence only; live and history device operations disabled.");
        throw new IllegalArgumentException("Unknown model");
    }
}
