import org.momhealth.gateway.WatchConnectionLease;
public final class WatchConnectionLeaseTests {
    public static void main(String[] args){
        Object manual=new Object(),automatic=new Object();
        check(WatchConnectionLease.acquire(manual));check(WatchConnectionLease.acquire(manual));
        check(!WatchConnectionLease.acquire(automatic));WatchConnectionLease.release(automatic);
        check(!WatchConnectionLease.acquire(automatic));WatchConnectionLease.release(manual);
        check(WatchConnectionLease.acquire(automatic));check(!WatchConnectionLease.acquire(manual));
        WatchConnectionLease.release(automatic);check(WatchConnectionLease.acquire(manual));WatchConnectionLease.release(manual);
        System.out.println("7 synthetic connection-exclusion checks passed.");
    }
    static void check(boolean value){if(!value)throw new AssertionError();}
}
