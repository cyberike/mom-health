package org.momhealth.gateway;

import java.util.Arrays;
import java.util.Locale;
import java.util.UUID;

/** Original implementation of the narrow protocol facts recorded in PROTOCOL.md. */
public final class V1Adapter implements DeviceAdapter {
    public String model() { return "V1 / 2208A"; }
    private UUID uuid(String code) { return UUID.fromString("0000" + code + "-0000-1000-8000-00805f9b34fb"); }
    public UUID service() { return uuid("fff0"); }
    public UUID writeCharacteristic() { return uuid("fff6"); }
    public UUID notifyCharacteristic() { return uuid("fff7"); }
    private byte[] packet(int opcode, boolean start) {
        byte[] out = new byte[16];
        out[0] = (byte) opcode;
        if (opcode == 0x28) {
            out[1] = 2; // HR only. No HRV, ECG, SpO2, BP or glucose requests.
            out[2] = (byte) (start ? 1 : 0);
            out[4] = (byte) (start ? 30 : 0); // Documented seconds; never demo's 120000.
        }
        int sum = 0;
        for (int i = 0; i < 15; i++) sum += out[i] & 255;
        out[15] = (byte) sum;
        return out;
    }
    public byte[] batteryRequest() { return packet(0x13, false); }
    public byte[] firmwareRequest() { return packet(0x27, false); }
    public byte[] startHeartRate() { return packet(0x28, true); }
    public byte[] stopHeartRate() { return packet(0x28, false); }
    public boolean permits(byte[] command) {
        return Arrays.equals(command, batteryRequest()) || Arrays.equals(command, firmwareRequest())
            || Arrays.equals(command, startHeartRate()) || Arrays.equals(command, stopHeartRate());
    }
    public Reading decode(byte[] p) {
        Reading unknown = new Reading(Reading.Kind.UNKNOWN, null, "Unrecognized/incomplete packet");
        if (p == null || p.length < 2 || p.length > 20) return unknown;
        int op = p[0] & 255;
        if (op == 0x13 && (p[1] & 255) <= 100) return new Reading(Reading.Kind.BATTERY, p[1] & 255, null);
        if (op == 0x27 && p.length >= 5) return new Reading(Reading.Kind.FIRMWARE, null,
            String.format(Locale.ROOT, "%X.%X.%X.%X", p[1] & 255, p[2] & 255, p[3] & 255, p[4] & 255));
        // Vendor callback reads through byte 9. Short ACKs and start/stop echoes are not observations.
        // Bounds are an engineering filter, not a clinical range or validity claim.
        if (op == 0x28 && p.length >= 10 && p[1] == 2 && (p[2] & 255) >= 20 && (p[2] & 255) <= 250)
            return new Reading(Reading.Kind.HEART_RATE, p[2] & 255, null);
        return unknown;
    }
    public static String hex(byte[] bytes) {
        StringBuilder text = new StringBuilder();
        for (byte b : bytes) { if (text.length() > 0) text.append(' '); text.append(String.format(Locale.ROOT, "%02X", b & 255)); }
        return text.toString();
    }
}
