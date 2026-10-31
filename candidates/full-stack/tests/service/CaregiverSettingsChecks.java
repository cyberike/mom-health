package org.momhealth.gateway;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;

/** Portable validation only. No Context, SharedPreferences, Keystore, BLE, service or network calls. */
public final class CaregiverSettingsChecks {
    private static int checks;
    private static void check(boolean value){checks++;if(!value)throw new AssertionError("check "+checks);}
    private static void invalid(Runnable action){checks++;try{action.run();}catch(IllegalArgumentException expected){return;}throw new AssertionError("Expected rejection "+checks);}
    private static RememberedWatch watch(){return new RememberedWatch("V8","AA:BB:CC:DD:EE:FF","Synthetic watch",UUID.randomUUID().toString());}
    private static CaregiverAutomationStore.Settings settings(boolean record,boolean upload,boolean restore,boolean remember,int retention,long uploadMs){return new CaregiverAutomationStore.Settings(watch(),60000,10,3,record,upload,uploadMs,upload?"https://example.invalid/v1/batches":"",restore,remember,retention);}
    public static void main(String[] args)throws Exception{
        CaregiverAutomationStore.Settings off=settings(false,false,false,false,0,60000);check(!off.recording&&!off.upload&&!off.restore&&!off.rememberToken);check(!off.policy(false).uploadConsent);
        invalid(()->settings(false,true,true,true,100,60000));invalid(()->settings(true,false,true,true,100,60000));
        invalid(()->settings(true,true,true,true,99,60000));invalid(()->settings(true,true,true,true,10001,60000));invalid(()->settings(true,true,true,true,100,59999));
        CaregiverAutomationStore.Settings job=settings(true,true,true,true,100,60000);check(CaregiverUploadJobService.eligible(job));check(CaregiverUploadJobService.actualInterval(job)==900000);check(job.policy(true).uploadConsent);check(!job.policy(false).uploadConsent);
        check(!CaregiverUploadJobService.eligible(settings(true,true,false,true,0,60000)));check(!CaregiverUploadJobService.eligible(settings(true,true,true,false,0,60000)));check(!CaregiverUploadJobService.eligible(off));check(CaregiverUploadJobService.actualInterval(settings(true,true,true,true,0,1800000))==1800000);
        invalid(()->new CaregiverAutomationStore.Settings(watch(),45000,10,3,true,false,60000,"",false,false,0));
        invalid(()->new CaregiverAutomationStore.Settings(watch(),60000,61,3,true,false,60000,"",false,false,0));
        invalid(()->new CaregiverAutomationStore.Settings(watch(),60000,10,7,true,false,60000,"",false,false,0));
        invalid(()->new CaregiverAutomationStore.Settings(watch(),60000,10,3,true,true,60000,"http://example.invalid/v1/batches",false,false,0));
        // Constructor validation performs no network request.
        new GatewayStatusClient("https://example.invalid/v1/batches","synthetic-not-a-deployed-credential");checks++;
        invalid(()->new GatewayStatusClient("https://user:password@example.invalid/v1/batches",null));
        invalid(()->new GatewayStatusClient("https://example.invalid/other",null));
        CountDownLatch start=new CountDownLatch(1),done=new CountDownLatch(12);AtomicInteger acquired=new AtomicInteger();
        for(int i=0;i<12;i++)new Thread(()->{try{start.await();if(CaregiverUploadJobService.acquireUpload())acquired.incrementAndGet();}catch(InterruptedException ex){Thread.currentThread().interrupt();}finally{done.countDown();}}).start();start.countDown();done.await();check(acquired.get()==1);CaregiverUploadJobService.releaseUpload();check(CaregiverUploadJobService.acquireUpload());CaregiverUploadJobService.releaseUpload();
        System.out.println(checks+" portable caregiver configuration/ownership assertions passed; no Android runtime or network used.");
    }
}
