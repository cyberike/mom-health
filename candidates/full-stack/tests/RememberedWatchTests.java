import org.momhealth.gateway.RememberedWatch;
public final class RememberedWatchTests {
    static int checks;
    static void check(boolean condition){checks++;if(!condition)throw new AssertionError();}
    static void reject(Runnable action){checks++;try{action.run();}catch(IllegalArgumentException expected){return;}throw new AssertionError("Expected rejection");}
    public static void main(String[] args){
        String id="00000000-0000-0000-0000-000000000001";
        RememberedWatch saved=new RememberedWatch("V8","aa:bb:cc:dd:ee:ff","Synthetic watch",id);
        check(saved.address.equals("AA:BB:CC:DD:EE:FF"));check(saved.deviceId.equals(id));
        check(saved.matches("V8","aa:bb:cc:dd:ee:ff"));check(!saved.matches("V1",saved.address));check(!saved.matches("V8","00:00:00:00:00:00"));
        RememberedWatch reopened=new RememberedWatch(saved.model,saved.address,saved.label,saved.deviceId);
        check(reopened.deviceId.equals(saved.deviceId));
        reject(()->new RememberedWatch("V4",saved.address,saved.label,id));
        reject(()->new RememberedWatch("V8","invalid",saved.label,id));
        reject(()->new RememberedWatch("V8",saved.address,"",id));
        reject(()->new RememberedWatch("V8",saved.address,saved.label,"not-a-uuid"));
        reject(()->new RememberedWatch("V8",null,saved.label,id));
        check(!saved.matches("V8",null));
        System.out.println(checks+" synthetic remembered-watch checks passed; no persistence or BLE execution.");
    }
}
