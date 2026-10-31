package org.momhealth.gateway;
/** Process-local exclusion across manual and automatic BLE controllers; no platform APIs. */
public final class WatchConnectionLease {
    private static Object owner;
    private WatchConnectionLease(){}
    public static synchronized boolean acquire(Object candidate){if(candidate==null)throw new IllegalArgumentException("Owner required");if(owner!=null&&owner!=candidate)return false;owner=candidate;return true;}
    public static synchronized void release(Object candidate){if(owner==candidate)owner=null;}
}
