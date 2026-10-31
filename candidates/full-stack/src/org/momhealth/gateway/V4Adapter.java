package org.momhealth.gateway;

import java.util.Arrays;
import java.util.Locale;
import java.util.UUID;

/** Inactive 2501/V4 protocol implementation. See V4_COMPONENT.md; no hardware validation. */
public final class V4Adapter implements DeviceAdapter {
    // Conservative engineering allocation bound, not a V4 firmware frame-length claim.
    public static final int MAX_FRAME_BYTES=512;
    public String model() { return "V4 / 2501"; }
    private UUID uuid(String code) { return UUID.fromString("0000"+code+"-0000-1000-8000-00805f9b34fb"); }
    public UUID service() { return uuid("fff0"); }
    public UUID writeCharacteristic() { return uuid("fff6"); }
    public UUID notifyCharacteristic() { return uuid("fff7"); }
    private byte[] packet(int opcode,int subtype,int enabled,int seconds) {
        byte[] p=new byte[16]; p[0]=(byte)opcode;p[1]=(byte)subtype;p[2]=(byte)enabled;
        p[4]=(byte)seconds;p[5]=(byte)(seconds>>>8);
        int sum=0;for(int i=0;i<15;i++)sum+=p[i]&255;p[15]=(byte)sum;return p;
    }
    public byte[] batteryRequest() { return packet(0x13,0,0,0); }
    public byte[] firmwareRequest() { return packet(0x27,0,0,0); }
    public byte[] startHeartRate() { return packet(0x28,2,1,30); }
    public byte[] stopHeartRate() { return packet(0x28,2,0,0); }
    // Manual demo uses opcode28 alone. Reporting is an optional distinct command, inactive here.
    public boolean needsReporting() { return false; }
    public byte[] enableReporting() { return packet(0x09,1,0,0); }
    public byte[] disableReporting() { return packet(0x09,0,0,0); }
    /** A later explicit reporting integration must require complete notifications and MTU >=27. */
    public static int minimumReportingMtu() { return 27; }
    public boolean permits(byte[] p) {
        return Arrays.equals(p,batteryRequest())||Arrays.equals(p,firmwareRequest())
            ||Arrays.equals(p,startHeartRate())||Arrays.equals(p,stopHeartRate())
            ||Arrays.equals(p,enableReporting())||Arrays.equals(p,disableReporting());
    }
    private Reading unknown(String detail) { return new Reading(Reading.Kind.UNKNOWN,null,detail); }
    private Reading hr(int value,String route) {
        if(value<20||value>250)return new Reading(Reading.Kind.NO_RESULT,null,"V4 "+route+" has no usable HR");
        return new Reading(Reading.Kind.HEART_RATE,value,"V4 "+route+" HR candidate; session verification required");
    }
    /** Stateless parser only; caller must reject stale/out-of-session frames. */
    public Reading decode(byte[] p) {
        if(p==null||p.length<2||p.length>MAX_FRAME_BYTES)return unknown("Incomplete/oversized V4 frame");
        switch(p[0]&255) {
            case 0x13:
                if((p[1]&255)>100)return unknown("Invalid battery percentage");
                return new Reading(Reading.Kind.BATTERY,p[1]&255,null);
            case 0x27:
                if(p.length<5)return unknown("Incomplete firmware prefix");
                return new Reading(Reading.Kind.FIRMWARE,null,String.format(Locale.ROOT,"%X.%X.%X.%X",p[1]&255,p[2]&255,p[3]&255,p[4]&255));
            case 0x28:
                if(p[1]!=2)return unknown("Unsupported measurement subtype");
                if(p.length<8)return unknown("Incomplete manual HR callback prefix");
                return hr(p[2]&255,"manual");
            case 0x09:
                if(p.length<24)return unknown("Incomplete activity callback prefix");
                return hr(p[21]&255,"activity");
            default:return unknown("Unrecognized V4 response");
        }
    }
}
