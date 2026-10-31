package org.momhealth.gateway;

/** Identical synthetic checks for each model. Prepared only; not hardware evidence. */
public final class ModelComparisonChecks {
    private static void require(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    public static void main(String[] args){
        DeviceAdapter[] models={new V1Adapter(),new V4Adapter(),new V8Adapter()};
        for(DeviceAdapter adapter:models){
            String model=adapter.model();
            require(adapter.service()!=null&&adapter.writeCharacteristic()!=null&&adapter.notifyCharacteristic()!=null,model+" UUID metadata");
            byte[][] allowed={adapter.batteryRequest(),adapter.firmwareRequest(),adapter.startHeartRate(),adapter.stopHeartRate()};
            for(byte[] command:allowed){
                require(command.length==16,model+" fixed command length");
                int sum=0;for(int i=0;i<15;i++)sum+=command[i]&255;
                require((command[15]&255)==(sum&255),model+" command checksum");
                require(adapter.permits(command),model+" allowlist accepts intended command");
                byte[] altered=command.clone();altered[10]=1;
                require(!adapter.permits(altered),model+" rejects modified command");
            }
            byte[] start=adapter.startHeartRate(),stop=adapter.stopHeartRate();
            require(start[0]==0x28&&start[1]==2&&start[2]==1&&(start[4]&255)==30,model+" bounded HR-only request");
            require(stop[0]==0x28&&stop[1]==2&&stop[2]==0,model+" stop does not open measurement");
            require(adapter.decode(null).kind==DeviceAdapter.Reading.Kind.UNKNOWN,model+" null frame");
            require(adapter.decode(new byte[0]).kind==DeviceAdapter.Reading.Kind.UNKNOWN,model+" empty frame");
            byte[] battery=new byte[16];battery[0]=0x13;battery[1]=95;
            require(adapter.decode(battery).kind==DeviceAdapter.Reading.Kind.BATTERY&&adapter.decode(battery).value==95,model+" battery prefix");
            battery[1]=(byte)255;require(adapter.decode(battery).kind!=DeviceAdapter.Reading.Kind.BATTERY,model+" impossible battery rejected");
            byte[] firmware=new byte[16];firmware[0]=0x27;firmware[3]=8;firmware[4]=8;
            require(adapter.decode(firmware).kind==DeviceAdapter.Reading.Kind.FIRMWARE,model+" firmware prefix");
            byte[] noResult=new byte[16];noResult[0]=0x28;noResult[1]=2;
            require(adapter.decode(noResult).kind!=DeviceAdapter.Reading.Kind.HEART_RATE,model+" zero not HR");
            byte[] forbidden=new byte[16];forbidden[0]=0x54;forbidden[1]=(byte)0x99;
            require(!adapter.permits(forbidden),model+" history deletion not live allowlist");
        }
    }
}
