package org.momhealth.gateway;
import android.app.job.*;
import android.content.*;
import android.os.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.momhealth.gateway.data.*;

/** Persisted constrained upload after explicit restore+upload+vault consent. No boot dataSync FGS. */
public final class CaregiverUploadJobService extends JobService {
    public static final int JOB_ID=612;
    private static final AtomicBoolean UPLOAD_OWNER=new AtomicBoolean();
    public static boolean acquireUpload(){return UPLOAD_OWNER.compareAndSet(false,true);}
    public static void releaseUpload(){UPLOAD_OWNER.set(false);}
    public static boolean eligible(CaregiverAutomationStore.Settings settings){return settings!=null&&settings.restore&&settings.upload&&settings.rememberToken;}
    public static long actualInterval(CaregiverAutomationStore.Settings settings){return Math.max(15*60000L,settings.uploadIntervalMs);}
    public static boolean schedule(Context c,CaregiverAutomationStore.Settings settings){
        JobScheduler scheduler=c.getSystemService(JobScheduler.class);if(!eligible(settings)){scheduler.cancel(JOB_ID);return true;}
        JobInfo info=new JobInfo.Builder(JOB_ID,new ComponentName(c,CaregiverUploadJobService.class)).setPersisted(true).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setRequiresBatteryNotLow(true).setRequiresStorageNotLow(true).setPeriodic(actualInterval(settings)).build();return scheduler.schedule(info)==JobScheduler.RESULT_SUCCESS;
    }
    public static void cancel(Context c){c.getSystemService(JobScheduler.class).cancel(JOB_ID);}
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final Handler main=new Handler(Looper.getMainLooper());
    private static final class Run {final JobParameters params;volatile boolean cancelled;volatile BatchUploader uploader;volatile GatewayStatusClient statusClient;Runnable deadline;boolean completionSent;Run(JobParameters params){this.params=params;}}
    private Run active;private boolean destroyed;
    @Override public boolean onStartJob(JobParameters params){
        if(active!=null||destroyed)return false;
        CaregiverAutomationStore config=new CaregiverAutomationStore(this);CaregiverAutomationStore.Settings settings;
        try{settings=config.read();if(!config.enabled()||!eligible(settings)||!getSystemService(UserManager.class).isUserUnlocked()||CaregiverService.handlesUploads()||!acquireUpload())return false;}catch(RuntimeException ex){config.status("Scheduled upload configuration unavailable. No data sent.");return false;}
        final Run run=new Run(params);active=run;run.deadline=()->{if(active!=run)return;cancelTransfer(run);complete(run);};main.postDelayed(run.deadline,25000);
        try{worker.submit(()->{HealthStore health=new HealthStore(this);BatchAck ack=null;try{
            String token=new CaregiverCredentialVault(this).read(settings.endpoint,settings.watch.deviceId);if(token==null)throw new IllegalStateException("Saved credential unavailable");if(run.cancelled||!config.enabled())return;
            BatchUploader transfer=new BatchUploader(settings.endpoint,false,token);GatewayStatusClient status=new GatewayStatusClient(settings.endpoint,token);run.uploader=transfer;run.statusClient=status;if(run.cancelled){transfer.cancel();status.cancel();return;}
            ack=SyncCoordinator.uploadOnce(health,transfer,settings.watch.deviceId);if(run.cancelled||!config.enabled())return;
            if(settings.uploadedRetentionLimit>0)health.pruneUploadedTo(settings.uploadedRetentionLimit,settings.watch.deviceId);
            GatewayStatusClient.LocalSnapshot counts=GatewayStatusClient.localSnapshot(health,settings.watch.deviceId);CaregiverService.TechnicalSnapshot technical=CaregiverService.technicalSnapshot(settings.watch.deviceId);
            status.send(settings.watch.deviceId,settings.watch.model,counts.lastObservation,technical.battery,technical.connection,counts.pending,counts.uploaded,technical.recording);
            if(!run.cancelled&&config.enabled())config.status(UploadOutcome.incomplete(ack)?UploadOutcome.incompleteMessage():"Scheduled upload attempt completed. Next constrained job is no sooner than "+(actualInterval(settings)/60000)+" minutes; Android may defer it.");
        }catch(Exception ex){if(!run.cancelled&&config.enabled())config.status(UploadOutcome.failureMessage(ack,"Scheduled upload did not complete. Pending data retained; review network or saved credential."));}
        finally{try{health.close();}finally{run.uploader=null;run.statusClient=null;releaseUpload();main.post(()->{main.removeCallbacks(run.deadline);if(active!=run)return;if(!run.cancelled)complete(run);active=null;});}}});return true;
        }catch(RejectedExecutionException ex){main.removeCallbacks(run.deadline);active=null;releaseUpload();config.status("Upload worker unavailable; pending data retained.");return false;}
    }
    private void complete(Run run){if(!destroyed&&!run.completionSent){run.completionSent=true;jobFinished(run.params,false);}}
    private void cancelTransfer(Run run){if(run==null)return;run.cancelled=true;BatchUploader up=run.uploader;if(up!=null)up.cancel();GatewayStatusClient status=run.statusClient;if(status!=null)status.cancel();}
    @Override public boolean onStopJob(JobParameters params){Run run=active;if(run!=null&&run.params.getJobId()==params.getJobId()){main.removeCallbacks(run.deadline);run.completionSent=true;cancelTransfer(run);}return false;}
    @Override public void onDestroy(){destroyed=true;cancelTransfer(active);main.removeCallbacksAndMessages(null);worker.shutdown();super.onDestroy();}
}
