import org.momhealth.gateway.*;
import java.util.Arrays;

public final class ProtocolTests {
    private static int count;
    private static void check(boolean condition, String description) {
        count++; if (!condition) throw new AssertionError(description);
    }
    public static void main(String[] args) {
        V1Adapter a = new V1Adapter();
        check(V1Adapter.hex(a.batteryRequest()).equals("13 00 00 00 00 00 00 00 00 00 00 00 00 00 00 13"), "battery packet");
        check(V1Adapter.hex(a.firmwareRequest()).equals("27 00 00 00 00 00 00 00 00 00 00 00 00 00 00 27"), "firmware packet");
        check(V1Adapter.hex(a.startHeartRate()).equals("28 02 01 00 1E 00 00 00 00 00 00 00 00 00 00 49"), "30-second HR");
        check(V1Adapter.hex(a.stopHeartRate()).equals("28 02 00 00 00 00 00 00 00 00 00 00 00 00 00 2A"), "stop HR");
        for (byte[] p : new byte[][]{a.batteryRequest(),a.firmwareRequest(),a.startHeartRate(),a.stopHeartRate()}) {
            check(a.permits(p), "allow exact command");
            for (int i=0;i<p.length;i++) { byte[] bad=p.clone(); bad[i]^=0x40; check(!a.permits(bad), "reject modified command"); }
        }
        check(!a.permits(null), "reject null");
        check(!a.permits(new byte[]{0x47}), "reject OTA");
        check(!a.permits(new byte[]{0x51,(byte)0x99}), "reject history delete");
        check(a.decode(new byte[]{0x13,80}).value == 80, "battery decode");
        check(a.decode(new byte[]{0x13,(byte)255}).value == null, "invalid battery is null");
        check(a.decode(new byte[]{0x27,1,2,0x10,4}).text.equals("1.2.10.4"), "version uses hex components");
        byte[] hr = new byte[16]; hr[0]=0x28; hr[1]=2; hr[2]=72;
        DeviceAdapter.Reading r=a.decode(hr);
        check(r.kind==DeviceAdapter.Reading.Kind.HEART_RATE && r.value==72, "HR decode");
        check(a.decode(a.startHeartRate()).value==null, "start echo is not HR");
        check(a.decode(a.stopHeartRate()).value==null, "stop echo is not HR");
        for (int n=0;n<10;n++) check(a.decode(Arrays.copyOf(hr,n)).value==null, "short HR rejected");
        check(a.decode(null).value==null, "null rejected");
        check(a.decode(Arrays.copyOf(hr,21)).value==null, "oversize rejected");
        byte[] wrong=hr.clone(); wrong[1]=1; check(a.decode(wrong).value==null,"HRV not promoted to HR");
        wrong=hr.clone(); wrong[0]=0x55; check(a.decode(wrong).value==null,"history not promoted to live HR");
        MeasurementWindow w=new MeasurementWindow();
        check(!w.accepts(0,0,r), "idle ignores HR");
        long first=w.begin(1000);
        check(w.accepts(first,1001,r), "active accepts candidate");
        check(!w.accepts(first,999,r), "pre-window rejected");
        check(!w.accepts(first,31000,r), "deadline is exclusive");
        check(!w.accepts(first,1100,a.decode(new byte[]{0x13,80})), "battery not HR");
        w.stop(); check(!w.accepts(first,1200,r), "stopped rejects HR");
        long second=w.begin(2000);
        check(!w.accepts(first,2001,r), "old generation rejected");
        check(w.accepts(second,2001,r), "new generation accepted");
        // Simulate a queued notification spanning stop/restart within the same clock tick.
        // Arrival time alone cannot distinguish these sessions.
        long queuedToken=w.generation();
        long queuedArrival=2000;
        w.stop();
        long replacement=w.begin(2000);
        check(!w.accepts(queuedToken,queuedArrival,r), "queued callback retains its old session token");
        check(w.accepts(replacement,queuedArrival,r), "same-tick current session remains valid");
        w.stop();
        check(!w.accepts(replacement,queuedArrival,r), "queued callback rejected after foreground stop");
        long pending=w.beginRequest(5000);
        check(!w.accepts(pending,5001,r),"no promotion before write confirmation");
        check(w.phase(pending,5001).equals("before start-write confirmation"),"pending diagnostic");
        w.confirm(pending,5100);
        check(!w.accepts(pending,5050,r),"queued pre-confirmation arrival remains excluded after callback");
        check(w.accepts(pending,34999,r),"last millisecond accepted");
        check(!w.accepts(pending,35000,r),"deadline stays anchored to submission");
        check(w.phase(pending,35000).contains("deadline"),"late diagnostic");
        w.stop();w.confirm(pending);
        check(!w.accepts(pending,6000,r),"late write callback cannot reopen stopped session");
        check(w.phase(pending,6000).equals("old session"),"old callback diagnostic");
        java.util.Random random=new java.util.Random(2208);
        for(int n=0;n<1000;n++) {
            byte[] fuzz=new byte[random.nextInt(80)];random.nextBytes(fuzz);
            check(a.decode(fuzz)!=null,"arbitrary notification must not crash");
        }
        System.out.println("PASS: " + count + " protocol/session assertions (synthetic fixtures; no hardware).");
    }
}
