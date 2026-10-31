import org.momhealth.gateway.*;
import java.util.Arrays;

public final class V8ProtocolTests {
    private static int count;
    private static void check(boolean ok,String reason) {count++;if(!ok)throw new AssertionError(reason);}
    public static void main(String[] args) {
        V8Adapter v8=new V8Adapter();V1Adapter v1=new V1Adapter();
        check(Arrays.equals(v8.startHeartRate(),v1.startHeartRate()),"30-second HR command is shared");
        check(Arrays.equals(v8.stopHeartRate(),hex("28 02 00 00 1E 00 00 00 00 00 00 00 00 00 00 48")),"V8 demo-aligned close retains duration30");
        check(Arrays.equals(v1.stopHeartRate(),hex("28 02 00 00 00 00 00 00 00 00 00 00 00 00 00 2A")),"V1 zero-duration stop preserved");
        check(!v8.permits(v1.stopHeartRate()),"old V8 zero-duration stop excluded");
        check(v8.permits(v8.stopHeartRate()),"new V8 stop allowlisted");
        check(Arrays.equals(v8.batteryRequest(),v1.batteryRequest()),"battery query shared");
        check(Arrays.equals(v8.firmwareRequest(),v1.firmwareRequest()),"firmware query shared");
        check(v8.service().equals(v1.service()) && v8.notifyCharacteristic().equals(v1.notifyCharacteristic()),"shared BLE identifiers");
        byte[] shortHr={0x28,2,72,0,0,0,0,0};
        check(v8.decode(shortHr).value==null,"manual command status is not the activity HR path");
        check(v1.decode(shortHr).value==null,"V1 remains unchanged");
        for(int n=0;n<8;n++)check(v8.decode(Arrays.copyOf(shortHr,n)).value==null,"short frame cannot supply HR");
        byte[] noData=new byte[16];noData[0]=0x28;noData[1]=2;noData[15]=0x2a;
        check(v8.decode(noData).value==null,"zero-HR response is not a measurement; synthetic fixture only");
        check(v8.decode(v8.startHeartRate()).value==null,"start echo not HR");
        check(v8.decode(v8.stopHeartRate()).value==null,"stop echo not HR");
        byte[] telemetry=new byte[25];telemetry[0]=9;telemetry[21]=72;
        check(v8.decode(telemetry).value==72,"complete activity frame supplies candidate; session gate required");
        for(int n=0;n<25;n++)check(v8.decode(Arrays.copyOf(telemetry,n)).value==null,"truncated activity rejected");
        check(v8.decode(Arrays.copyOf(telemetry,26)).value==null,"oversized activity rejected");
        for(int hr=0;hr<256;hr++) {
            telemetry[21]=(byte)hr;
            check((v8.decode(telemetry).value!=null)==(hr>=20 && hr<=250),"HR bounds including zero");
        }
        // Synthetic 32-byte prefix fixtures; screenshot disclosed length, not HR payload.
        for(int length:new int[]{25,32}) {
            byte[] frame=new byte[length];frame[0]=9;
            for(int hr=0;hr<256;hr++) {
                frame[21]=(byte)hr;
                check((v8.decode(frame).value!=null)==(hr>=20 && hr<=250),"both supported lengths enforce HR bounds");
            }
        }
        byte[] extended=new byte[32];extended[0]=9;extended[21]=73;
        for(int pos=25;pos<32;pos++)for(int value=0;value<256;value++) {
            extended[pos]=(byte)value;
            check(v8.decode(extended).value==73,"trailing bytes never become HR or alter offset");
        }
        extended[21]=0;extended[31]=72;
        check(v8.decode(extended).kind==DeviceAdapter.Reading.Kind.NO_RESULT,"last byte never substitutes for zero HR");
        extended[21]=74;
        for(int length=0;length<=64;length++) {
            byte[] frame=Arrays.copyOf(extended,length);
            check((v8.decode(frame).value!=null)==(length==25 || length==32),"unknown length excluded without fragmentation guessing");
        }
        for(int opcode=0;opcode<256;opcode++) {
            extended[0]=(byte)opcode;
            check((v8.decode(extended).kind==DeviceAdapter.Reading.Kind.HEART_RATE)==(opcode==9),"only activity opcode supplies extended HR");
        }
        check(v8.decode(null).value==null,"null packet safe");
        check(v8.minimumMtu()==35 && v8.needsReporting(),"V8 complete notification prerequisite");
        check(Arrays.equals(v8.enableReporting(),hex("09 01 00 00 00 00 00 00 00 00 00 00 00 00 00 0A")),"temperature-disabled enable");
        check(Arrays.equals(v8.disableReporting(),hex("09 00 00 00 00 00 00 00 00 00 00 00 00 00 00 09")),"disable");
        for(byte[] command:new byte[][]{v8.enableReporting(),v8.disableReporting()}) {
            check(v8.permits(command),"exact reporting allowlist");
            for(int i=0;i<16;i++) {byte[] changed=command.clone();changed[i]^=2;check(!v8.permits(changed),"mutated reporting command rejected");}
        }
        byte[] wrong=shortHr.clone();wrong[1]=1;check(v8.decode(wrong).value==null,"HRV excluded");
        check(!v8.permits(new byte[]{9,1}),"telemetry enable not in allowlist");
        check(!v8.permits(new byte[]{0x47}),"OTA excluded");
        check(!v8.permits(new byte[]{0x51,(byte)0x99}),"delete excluded");
        check(v8.decode(new byte[]{0x13,80,0,0,0}).value==80,"V8 battery layout");
        check(v8.decode(new byte[]{0x13,80}).value==null,"incomplete V8 metadata");
        check(v8.decode(new byte[]{0x27,1,2,0x10,4}).text.equals("1.2.10.4"),"firmware layout");
        // Screenshot transcription supplied by parent; not a new hardware capture.
        check(v8.decode(hex("27 00 00 08 08 26 06 04 01 00 19 06 06 00 09 96")).text.equals("0.0.8.8"),"transcribed firmware");
        check(v8.decode(hex("13 64 00 42 58 42 58 64 64 64 00 00 00 00 00 D7")).value==100,"transcribed battery");
        check(v8.decode(hex("28 02 00 00 00 00 00 00 00 00 00 00 00 00 00 2A")).kind==DeviceAdapter.Reading.Kind.NO_RESULT,"zero reply yields no result, never 42 bpm");
        check(v8.decode(hex("28 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 28")).value==null,"stop reply is not HR");
        java.util.Random random=new java.util.Random(2436);
        for(int i=0;i<1000;i++) {byte[] p=new byte[random.nextInt(80)];random.nextBytes(p);check(v8.decode(p)!=null,"malformed input safe");}
        System.out.println("PASS: "+count+" V8 assertions (synthetic plus screenshot-transcribed fixtures); no new hardware validation.");
    }
    private static byte[] hex(String text) {
        String[] fields=text.split(" ");byte[] out=new byte[fields.length];
        for(int i=0;i<fields.length;i++)out[i]=(byte)Integer.parseInt(fields[i],16);return out;
    }
}
