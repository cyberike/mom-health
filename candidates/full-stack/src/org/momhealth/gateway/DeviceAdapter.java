package org.momhealth.gateway;

import java.util.UUID;

/** Model boundary. V4/V8 must supply separate, independently tested adapters. */
public interface DeviceAdapter {
    String model();
    UUID service();
    UUID writeCharacteristic();
    UUID notifyCharacteristic();
    byte[] batteryRequest();
    byte[] firmwareRequest();
    byte[] startHeartRate();
    byte[] stopHeartRate();
    default boolean needsReporting() { return false; }
    default int minimumMtu() { return 23; }
    default byte[] enableReporting() { throw new UnsupportedOperationException(); }
    default byte[] disableReporting() { throw new UnsupportedOperationException(); }
    boolean permits(byte[] command);
    Reading decode(byte[] packet);

    final class Reading {
        public enum Kind { BATTERY, FIRMWARE, HEART_RATE, NO_RESULT, UNKNOWN }
        public final Kind kind;
        public final Integer value;
        public final String text;
        public Reading(Kind kind, Integer value, String text) {
            this.kind = kind; this.value = value; this.text = text;
        }
    }
}
