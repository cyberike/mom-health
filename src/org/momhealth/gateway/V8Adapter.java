package org.momhealth.gateway;

import java.util.Arrays;
import java.util.Locale;
import java.util.UUID;

/** V8 manual HR protocol adapter; hardware behavior remains unverified. */
public final class V8Adapter implements DeviceAdapter {
    public String model() { return "V8"; }
    private UUID uuid(String id) {return UUID.fromString("0000"+id+"-0000-1000-8000-00805f9b34fb");}
    public UUID service() {return uuid("fff0");}
    public UUID writeCharacteristic() {return uuid("fff6");}
    public UUID notifyCharacteristic() {return uuid("fff7");}
    // V8 demo uses measurementTime=30 for open=true AND open=false.
    private byte[] command(int opcode,boolean start) {
        byte[] p=new byte[16];p[0]=(byte)opcode;
        if(opcode==0x28) {p[1]=2;p[2]=(byte)(start?1:0);p[4]=30;}
        int sum=0;for(int i=0;i<15;i++)sum+=p[i]&255;p[15]=(byte)sum;return p;
    }
    public byte[] batteryRequest() {return command(0x13,false);}
    public byte[] firmwareRequest() {return command(0x27,false);}
    public byte[] startHeartRate() {return command(0x28,true);}
    public byte[] stopHeartRate() {return command(0x28,false);}
    public boolean needsReporting() {return true;}
    public int minimumMtu() {return 35;}
    public byte[] enableReporting() {byte[] p=command(9,false);p[1]=1;p[15]=10;return p;}
    public byte[] disableReporting() {return command(9,false);}
    public boolean permits(byte[] p) {
        return Arrays.equals(p,batteryRequest()) || Arrays.equals(p,firmwareRequest())
            || Arrays.equals(p,startHeartRate()) || Arrays.equals(p,stopHeartRate())
            || Arrays.equals(p,enableReporting()) || Arrays.equals(p,disableReporting());
    }
    // SDK consumes a fixed prefix through byte 24; observed firmware sends 32 bytes.
    // Remaining bytes are uninterpreted, not checksum or additional measurements.
    public static boolean supportedActivityLength(int length) {return length==25 || length==32;}
    public Reading decode(byte[] p) {
        if(p==null || p.length<2 || p.length>32)return unknown("Incomplete/unsupported V8 frame");
        int op=p[0]&255;
        if(op==0x09) {
            if(!supportedActivityLength(p.length))return unknown("Unsupported activity length; expected 25 or observed 32 bytes");
            int hr=p[21]&255;
            if(hr>=20 && hr<=250)return new Reading(Reading.Kind.HEART_RATE,hr,"V8 activity HR candidate");
            return new Reading(Reading.Kind.NO_RESULT,null,"Activity frame has no usable HR yet");
        }
        if(p.length>20)return unknown("Unsupported length for manual V8 response");
        if(op==0x13 && p.length>=5 && (p[1]&255)<=100)
            return new Reading(Reading.Kind.BATTERY,p[1]&255,null);
        if(op==0x27 && p.length>=5)return new Reading(Reading.Kind.FIRMWARE,null,
            String.format(Locale.ROOT,"%X.%X.%X.%X",p[1]&255,p[2]&255,p[3]&255,p[4]&255));
        // V8 SDK manual HR callback accesses bytes 0..7, unlike V1's bytes 0..9.
        // This is a decoder bound, not evidence that a physical V8 sends 8-byte frames.
        if(op==0x28 && p[1]==2 && p.length>=8) {
            return new Reading(Reading.Kind.NO_RESULT,null,"Manual HR command status; waiting for activity HR data");
        }
        return unknown("Unrecognized V8 response");
    }
    private Reading unknown(String reason) {return new Reading(Reading.Kind.UNKNOWN,null,reason);}
}
