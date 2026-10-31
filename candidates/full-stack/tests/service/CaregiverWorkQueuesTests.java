package org.momhealth.gateway;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.RejectedExecutionException;

/** Synthetic concurrency checks. Does not invoke Android, network, or a real reading. */
public final class CaregiverWorkQueuesTests {
    private static void check(boolean ok,String name){if(!ok)throw new AssertionError(name);}
    public static void main(String[] args)throws Exception{
        CaregiverWorkQueues q=new CaregiverWorkQueues();
        CountDownLatch started=new CountDownLatch(1),release=new CountDownLatch(1),stored=new CountDownLatch(1);
        try {
            q.upload(()->{started.countDown();try{release.await();}catch(InterruptedException ex){Thread.currentThread().interrupt();}});
            check(started.await(2,TimeUnit.SECONDS),"upload starts");
            q.capture(stored::countDown);
            check(stored.await(2,TimeUnit.SECONDS),"blocked upload must not block local capture");
            q.upload(()->{}); // one bounded waiting job
            try { q.upload(()->{}); throw new AssertionError("unbounded upload queue"); }
            catch(RejectedExecutionException expected) { }
        } finally {release.countDown();q.shutdown();}
        check(q.awaitTermination(3,TimeUnit.SECONDS),"both lanes terminate");
        try {q.capture(()->{});throw new AssertionError("capture after shutdown");}
        catch(RejectedExecutionException expected) { }
        CaregiverWorkQueues draining=new CaregiverWorkQueues();
        CountDownLatch gate=new CountDownLatch(1), began=new CountDownLatch(1), committed=new CountDownLatch(1);
        draining.capture(()->{began.countDown();try{gate.await();}catch(InterruptedException ex){Thread.currentThread().interrupt();}});
        check(began.await(2,TimeUnit.SECONDS),"first capture starts");
        draining.capture(committed::countDown);
        draining.shutdown();gate.countDown();
        check(draining.awaitTermination(3,TimeUnit.SECONDS),"capture drains after orderly shutdown");
        check(committed.getCount()==0,"accepted queued capture must run before shutdown completes");
        System.out.println("8 caregiver queue assertions passed; synthetic concurrency only.");
    }
}
