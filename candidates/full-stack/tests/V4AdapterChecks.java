package org.momhealth.gateway;

/** Synthetic source checks only. Not executed; contains no transport or device operations. */
public final class V4AdapterChecks {
    private static void check(boolean value) {if(!value)throw new AssertionError();}
    public static void runChecks() {
        V4Adapter a=new V4Adapter();
        check(a.service().toString().startsWith("0000fff0"));
        check(a.writeCharacteristic().toString().startsWith("0000fff6"));
        check(a.notifyCharacteristic().toString().startsWith("0000fff7"));
        check(!a.needsReporting());check(V4Adapter.minimumReportingMtu()==27);
        byte[][] commands={a.batteryRequest(),a.firmwareRequest(),a.startHeartRate(),a.stopHeartRate(),a.enableReporting(),a.disableReporting()};
        for(byte[] p:commands) {
            check(p.length==16);check(a.permits(p));int sum=0;for(int i=0;i<15;i++)sum+=p[i]&255;check((sum&255)==(p[15]&255));
            for(int i=0;i<p.length;i++){byte[] changed=p.clone();changed[i]^=0x40;check(!a.permits(changed));}
        }
        check((a.startHeartRate()[15]&255)==0x49);check((a.stopHeartRate()[15]&255)==0x2a);
        check(a.startHeartRate()[4]==30&&a.stopHeartRate()[4]==0&&a.enableReporting()[2]==0);
        check(!a.permits(null));check(!a.permits(new byte[16]));
        check(a.decode(null).kind==DeviceAdapter.Reading.Kind.UNKNOWN);
        check(a.decode(new byte[]{0x13,100}).value==100);
        check(a.decode(new byte[]{0x13,101}).kind==DeviceAdapter.Reading.Kind.UNKNOWN);
        check("0.0.8.8".equals(a.decode(new byte[]{0x27,0,0,8,8}).text));
        check(a.decode(new byte[]{0x27,0,0,8}).kind==DeviceAdapter.Reading.Kind.UNKNOWN);
        for(int n=2;n<8;n++){byte[] p=new byte[n];p[0]=0x28;p[1]=2;check(a.decode(p).kind==DeviceAdapter.Reading.Kind.UNKNOWN);}
        for(int n:new int[]{8,16,20,32,512}) {byte[] p=new byte[n];p[0]=0x28;p[1]=2;p[2]=82;check(a.decode(p).value==82);}
        byte[] manual=new byte[16];manual[0]=0x28;manual[1]=2;
        for(int hr:new int[]{0,1,19,251,255}){manual[2]=(byte)hr;check(a.decode(manual).kind==DeviceAdapter.Reading.Kind.NO_RESULT);}
        manual[1]=1;manual[2]=82;check(a.decode(manual).kind==DeviceAdapter.Reading.Kind.UNKNOWN);
        for(int n=2;n<24;n++){byte[] p=new byte[n];p[0]=9;check(a.decode(p).kind==DeviceAdapter.Reading.Kind.UNKNOWN);}
        for(int n:new int[]{24,25,32,512}){byte[] p=new byte[n];p[0]=9;p[21]=82;check(a.decode(p).value==82);p[21]=0;check(a.decode(p).kind==DeviceAdapter.Reading.Kind.NO_RESULT);}
        byte[] oversized=new byte[513];oversized[0]=9;oversized[21]=82;check(a.decode(oversized).kind==DeviceAdapter.Reading.Kind.UNKNOWN);
        check(a.decode(a.startHeartRate()).kind==DeviceAdapter.Reading.Kind.NO_RESULT);
        check(a.decode(a.stopHeartRate()).kind==DeviceAdapter.Reading.Kind.NO_RESULT);
    }
}
