package org.momhealth.gateway;

import android.Manifest;
import android.app.*;
import android.bluetooth.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.net.*;
import android.os.*;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.*;
import org.momhealth.gateway.automation.*;
import org.momhealth.gateway.data.*;

/** Explicitly consented foreground owner of BLE. Never starts during build or installation. */
public final class CaregiverService extends Service implements BleController.Listener {
    public static final String START="org.momhealth.gateway.automation.START",STOP="org.momhealth.gateway.automation.STOP",RESTORE="org.momhealth.gateway.automation.RESTORE";
    public static final String TOKEN="runtime_token";
    private static final String CHANNEL="caregiver-operation";
    private static final int NOTIFICATION=610;
    private static volatile boolean running,reserved;
    private static volatile CaregiverService current;
    public static boolean handlesUploads(){CaregiverService s=current;return s!=null&&!s.stopping&&s.settings!=null&&s.settings.upload&&s.token!=null&&!s.settingsStore.bootUploadPaused();}
    public static final class TechnicalSnapshot{public final Integer battery;public final String connection;public final boolean recording;private TechnicalSnapshot(Integer b,String c,boolean r){battery=b;connection=c;recording=r;}}
    public static TechnicalSnapshot technicalSnapshot(String deviceId){CaregiverService s=current;if(s==null||s.settings==null||!s.settings.watch.deviceId.equals(deviceId)||s.stopping)return new TechnicalSnapshot(null,"disconnected",false);return new TechnicalSnapshot(s.battery,s.connected?"connected":s.connectOp!=0?"connecting":"disconnected",s.settings.recording);}
    public static boolean isRunning(){return running||reserved;}
    public static final class SavedReading {public final int bpm;public final long receivedAt;private SavedReading(int bpm,long at){this.bpm=bpm;this.receivedAt=at;}}
    /** In-memory view of this service session; it never exposes a past session as live. */
    public static SavedReading lastSavedReading(){CaregiverService s=current;return s==null||s.stopping?null:s.lastSavedReading;}
    public static synchronized boolean reserveStart(){if(isRunning())return false;reserved=true;return true;}
    public static synchronized void releaseReservation(){if(!running)reserved=false;}
    private final Handler main=new Handler(Looper.getMainLooper());
    private final AutomationEngine engine=new AutomationEngine();
    private final CaregiverWorkQueues worker=new CaregiverWorkQueues();
    private CaregiverAutomationStore settingsStore;
    private CaregiverAutomationStore.Settings settings;
    private HealthStore health;
    private BleController ble;
    private PowerManager.WakeLock workLock,networkLock;
    private volatile BatchUploader uploader;
    private volatile GatewayStatusClient statusClient;
    private volatile boolean stopping;
    private boolean foreground,draining,networkBusy,identityRequested;
    private volatile boolean connected;
    private long connectGen,connectOp,measurementGen,measurementOp;
    private String measurementId,lastObservation,reason="Starting caregiver service",lastPublished;
    private volatile String token;
    private volatile Integer battery;
    private volatile SavedReading lastSavedReading;
    private long budgetDelayMs;
    private final Runnable ticker=new Runnable(){public void run(){if(stopping)return;try{availability();settleConnection();engine.tick(now());drain();publish();}catch(RuntimeException ex){stopOperation("Automation stopped: invalid state or permission. Caregiver review required.");}if(!stopping)main.postDelayed(this,1000);}};
    private long now(){return SystemClock.elapsedRealtime();}
    @Override public void onCreate(){super.onCreate();settingsStore=new CaregiverAutomationStore(this);health=new HealthStore(this);PowerManager pm=getSystemService(PowerManager.class);workLock=pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"MomHealth:bounded-operation");workLock.setReferenceCounted(false);networkLock=pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"MomHealth:bounded-upload");networkLock.setReferenceCounted(false);getSystemService(NotificationManager.class).createNotificationChannel(new NotificationChannel(CHANNEL,"caregiver watch operation",NotificationManager.IMPORTANCE_LOW));}
    public static boolean prerequisites(Context c){return c.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)==PackageManager.PERMISSION_GRANTED&&(Build.VERSION.SDK_INT<33||c.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED)&&c.getSystemService(NotificationManager.class).areNotificationsEnabled();}
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent!=null&&STOP.equals(intent.getAction())){stopOperation("Stopped by caregiver. Automatic work disabled.");return START_NOT_STICKY;}
        if(running){publish();return settings!=null&&settings.restore?START_STICKY:START_NOT_STICKY;}
        try{
            settings=settingsStore.read();boolean restore=intent==null||RESTORE.equals(intent.getAction());
            if(settingsStore.shutdownUnconfirmed()){settingsStore.setEnabled(false);settingsStore.status("Previous measurement shutdown was unconfirmed. Caregiver must inspect the watch and explicitly acknowledge review before restarting.");reserved=false;stopSelf();return START_NOT_STICKY;}
            if(settings==null||!settingsStore.enabled()||!prerequisites(this)||(restore&&!settings.restore)||(!restore&&(intent==null||!START.equals(intent.getAction())))){
                settingsStore.status("Automation is not running. Caregiver setup/permission review required.");reserved=false;stopSelf();return START_NOT_STICKY;
            }
            token=restore?null:intent.getStringExtra(TOKEN);
            if(token!=null&&token.isEmpty())token=null;
            if(token==null&&settings.upload&&settings.rememberToken)try{token=new CaregiverCredentialVault(this).read(settings.endpoint,settings.watch.deviceId);}catch(Exception ex){reason="Saved credential unavailable; upload paused. Local collection can continue.";}
            if(settings.upload&&token!=null)new BatchUploader(settings.endpoint,false,token);
            promote(false);running=true;reserved=false;current=this;health.setRecordingEnabled(settings.recording);
            ble=new BleController(this,"V8".equals(settings.watch.model)?new V8Adapter():new V1Adapter(),this);
            boolean uploadReady=settings.upload&&token!=null&&!settingsStore.bootUploadPaused();
            engine.enable(settings.policy(uploadReady),now());availability();drain();
            reason=restore?"Restored caregiver consent; request budget retained.":"Caregiver operation enabled.";
            main.removeCallbacks(ticker);main.post(ticker);publish();return settings.restore?START_STICKY:START_NOT_STICKY;
        }catch(RuntimeException ex){settingsStore.setEnabled(false);reason="Foreground operation could not start. Review permissions and saved settings.";settingsStore.status(reason);reserved=false;running=false;stopSelf();return START_NOT_STICKY;}
    }
    private void availability(){
        BluetoothManager manager=getSystemService(BluetoothManager.class);BluetoothAdapter adapter=manager==null?null:manager.getAdapter();
        boolean radio=prerequisites(this)&&adapter!=null&&adapter.isEnabled();
        ConnectivityManager cm=getSystemService(ConnectivityManager.class);NetworkCapabilities nc=cm.getNetworkCapabilities(cm.getActiveNetwork());boolean network=nc!=null&&nc.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
        PowerManager pm=getSystemService(PowerManager.class);BatteryManager bm=getSystemService(BatteryManager.class);int level=bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        boolean power=!pm.isPowerSaveMode()&&(level<0||level>15);
        // A durable acquisition budget must not suppress separately consented uploads.
        // Deferral changes only the next request, never the current 30-second window.
        long elapsed=now();
        long delay=settingsStore.delayUntilAllowed(settings,System.currentTimeMillis());
        budgetDelayMs=delay;
        if(delay>0)engine.deferAcquisitionUntil(engine.generation(),elapsed+delay,elapsed);
        engine.environment(radio,network,power,elapsed);
    }
    private void drain(){
        if(draining)return;draining=true;
        try{for(int rounds=0;rounds<12;rounds++){
            java.util.List<AutomationEngine.Action> actions=engine.drainActions();if(actions.isEmpty())break;
            for(AutomationEngine.Action action:actions)switch(action.type){
                case CONNECT:
                    if(stopping||action.generation!=engine.generation())break;
                    connectGen=action.generation;connectOp=action.operationId;connected=false;identityRequested=false;wake(22000);
                    BluetoothAdapter adapter=getSystemService(BluetoothManager.class).getAdapter();
                    try{ble.connect(adapter.getRemoteDevice(settings.watch.address));}catch(RuntimeException ex){engine.connectionResult(connectGen,connectOp,false,now());connectOp=0;}
                    break;
                case START_MEASUREMENT:
                    if(stopping||action.generation!=engine.generation())break;
                    if(!settingsStore.markStarted(settings,System.currentTimeMillis())){engine.suspend(now());reason="Persisted acquisition budget blocked a request; caregiver review required.";break;}
                    measurementGen=action.generation;measurementOp=action.operationId;measurementId=UUID.randomUUID().toString();wake(60000);ble.startHeartRate();break;
                case STOP_MEASUREMENT: if(ble!=null)ble.stopHeartRate();break;
                case DISCONNECT: if(ble!=null)ble.disconnect();connected=false;connectOp=0;break;
                case UPLOAD_BATCH: if(!stopping&&action.generation==engine.generation())upload(action);break;
                case CANCEL_UPLOAD: cancelUpload();break;
            }
        }}finally{draining=false;}
    }
    private void wake(long millis){if(workLock!=null)workLock.acquire(millis);}
    private void releaseWake(){if(workLock!=null&&workLock.isHeld())workLock.release();}
    private void releaseNetworkWake(){if(networkLock!=null&&networkLock.isHeld())networkLock.release();}
    private void settleConnection(){if(connectOp==0||ble==null||stopping)return;if(!ble.hasConnection()){long op=connectOp;connectOp=0;engine.connectionResult(connectGen,op,false,now());return;}if(!ble.readyForMeasurement())return;if(!identityRequested){identityRequested=true;ble.readIdentity();return;}connected=true;long op=connectOp;connectOp=0;engine.connectionResult(connectGen,op,true,now());}
    @Override public void state(boolean ready,boolean measuring){main.post(()->{
        if(ble==null)return;
        if(stopping){if(!ble.hasConnection())finishStop();return;}
        if(connectOp!=0){
            if(ready){settleConnection();}
            else if(!ble.hasConnection()){long op=connectOp;connectOp=0;engine.connectionResult(connectGen,op,false,now());}
        }else if(connected&&!ble.hasConnection()){connected=false;engine.connectionLost(engine.generation(),now());measurementOp=0;measurementId=null;releaseWake();}
        if(measurementOp!=0&&ble.measurementConfirmed())engine.measurementStarted(measurementGen,measurementOp,now());
        drain();publish();
    });}
    @Override public void measurementFinished(boolean confirmed){main.post(()->{
        if(measurementOp!=0)engine.measurementFinished(measurementGen,measurementOp,confirmed,now());
        if(confirmed){if(!settingsStore.confirmCleanup()){stopOperation("Could not persist cleanup confirmation. Caregiver review required.");return;}measurementOp=0;measurementId=null;releaseWake();}
        if(stopping){if(ble!=null)ble.disconnect();if(ble==null||!ble.hasConnection())finishStop();return;}
        reason=confirmed?"Stop writes delivered; physical sensor shutdown is not independently confirmed.":"Stop delivery is unconfirmed. Caregiver review required.";
        drain();publish();
    });}
    @Override public void event(String message){
        // Do not persist raw packets, HR values, addresses or high-frequency log text.
        if(message.contains("permission")||message.contains("failed")||message.contains("timed out")||message.contains("unconfirmed")){reason="Watch operation needs review: "+message;publish();}
    }
    @Override public void reading(DeviceAdapter.Reading reading,String raw,long receivedAt){
        if(reading.kind==DeviceAdapter.Reading.Kind.BATTERY){battery=reading.value;return;}
        if(stopping||reading.kind!=DeviceAdapter.Reading.Kind.HEART_RATE||measurementId==null||!engine.acceptsObservation(measurementGen,measurementOp,now()))return;
        final String session=measurementId;
        final HealthSample sample;
        try{sample=new HealthSample(UUID.randomUUID().toString(),settings.watch.deviceId,session,settings.watch.model,"live",Instant.ofEpochMilli(receivedAt).toString(),null,reading.value,null,false);}catch(RuntimeException ex){return;}
        lastObservation=sample.receivedAt;
        // Consent and session validity were checked above, before this item entered the queue.
        // Stop prevents future acceptance; it must not silently discard an already accepted observation.
        try{worker.capture(()->{try{if(health.append(sample))main.post(()->{if(current==this&&!stopping)lastSavedReading=new SavedReading(sample.heartRateBpm,receivedAt);});}catch(RuntimeException ex){String failure="A previously accepted reading could not be saved. Existing pending data was preserved; caregiver review required.";if(stopping)settingsStore.status(failure);else main.post(()->stopOperation(failure));}});}catch(RejectedExecutionException ex){stopOperation("Local work queue full. An accepted reading could not be saved; caregiver review required.");}
    }
    private void upload(AutomationEngine.Action action){
        if(networkBusy||token==null||!CaregiverUploadJobService.acquireUpload()){engine.uploadFinished(action.generation,action.operationId,false,now());return;}
        final BatchUploader transfer=new BatchUploader(settings.endpoint,false,token);final GatewayStatusClient statusTransfer=new GatewayStatusClient(settings.endpoint,token);
        uploader=transfer;statusClient=statusTransfer;networkBusy=true;try{promote(true);networkLock.acquire(18000);}catch(RuntimeException ex){networkBusy=false;CaregiverUploadJobService.releaseUpload();engine.uploadFinished(action.generation,action.operationId,false,now());return;}
        final String last=lastObservation;final Integer batteryValue=battery;final boolean connectedSnapshot=connected;
        try{worker.upload(()->{boolean success=false;BatchAck ack=null;try{
            if(stopping||action.generation!=engine.generation())return;
            ack=SyncCoordinator.uploadOnce(health,transfer,settings.watch.deviceId);
            if(transfer.isCancelled()||stopping)return;
            if(settings.uploadedRetentionLimit>0)health.pruneUploadedTo(settings.uploadedRetentionLimit,settings.watch.deviceId);
            GatewayStatusClient.LocalSnapshot counts=GatewayStatusClient.localSnapshot(health,settings.watch.deviceId);
            statusTransfer.send(settings.watch.deviceId,settings.watch.model,counts.lastObservation,batteryValue,connectedSnapshot?"connected":"disconnected",counts.pending,counts.uploaded,settings.recording);
            // HTTP success is not enough: rejected or omitted items remain pending.
            success=ack==null||ack.allCommitted();
        }catch(Exception ex){/* No payload, token or endpoint is written to logs. */}
        finally{CaregiverUploadJobService.releaseUpload();final boolean result=success;final BatchAck outcome=ack;main.post(()->{if(uploader==transfer)uploader=null;if(statusClient==statusTransfer)statusClient=null;networkBusy=false;boolean accepted=engine.uploadFinished(action.generation,action.operationId,result,now());if(!stopping){if(accepted)reason=UploadOutcome.foregroundMessage(reason,outcome,result);promote(false);drain();publish();}releaseNetworkWake();});}});
        }catch(RejectedExecutionException ex){networkBusy=false;cancelUpload();promote(false);releaseNetworkWake();CaregiverUploadJobService.releaseUpload();engine.uploadFinished(action.generation,action.operationId,false,now());}
    }
    private void cancelUpload(){BatchUploader current=uploader;if(current!=null)current.cancel();GatewayStatusClient status=statusClient;if(status!=null)status.cancel();}
    private Notification notification(){
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,CaregiverAutomationActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop=PendingIntent.getService(this,1,new Intent(this,CaregiverService.class).setAction(STOP),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_menu_info_details).setContentTitle(stopping?"Mom Health stopping":"Mom Health caregiver operation").setContentText(stopping?reason:engine.status()).setStyle(new Notification.BigTextStyle().bigText(reason)).setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true).addAction(new Notification.Action.Builder(null,"Stop automatic work",stop).build()).build();
    }
    private void promote(boolean sync){int type=ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE;if(sync)type|=ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC;startForeground(NOTIFICATION,notification(),type);foreground=true;}
    private void publish(){if(settingsStore==null)return;String value=reason+"\n"+engine.status();if(settings!=null){String budget=CaregiverStatusDisplay.budget(budgetDelayMs,measurementOp!=0,engine.state()==AutomationEngine.State.ATTENTION);if(!budget.isEmpty())value+="\n"+budget;value+="\n"+CaregiverStatusDisplay.upload(settings.upload,token!=null,settingsStore.bootUploadPaused());}if(value.equals(lastPublished))return;lastPublished=value;settingsStore.status(value);if(foreground)getSystemService(NotificationManager.class).notify(NOTIFICATION,notification());}
    private void stopOperation(String why){
        if(stopping)return;stopping=true;reason=why;settingsStore.setEnabled(false);CaregiverUploadJobService.cancel(this);engine.revoke(now());main.removeCallbacks(ticker);cancelUpload();drain();if(ble!=null)ble.disconnect();publish();
        if(ble==null||!ble.hasConnection())finishStop();else main.postDelayed(()->{reason=why+" Cleanup deadline reached; physical shutdown cannot be guaranteed.";finishStop();},17000);
    }
    private void finishStop(){settingsStore.status(reason);running=false;reserved=false;token=null;releaseWake();releaseNetworkWake();if(foreground){stopForeground(STOP_FOREGROUND_REMOVE);foreground=false;}stopSelf();}
    /** Android 15 data-sync timeout: cancel upload and retain samples; never extend it. */
    @Override public void onTimeout(int startId,int fgsType){cancelUpload();stopOperation("Android foreground-service time budget ended. Pending data retained.");}
    @Override public void onDestroy(){stopping=true;main.removeCallbacksAndMessages(null);cancelUpload();if(ble!=null)ble.disconnect();token=null;running=false;reserved=false;if(current==this)current=null;releaseWake();releaseNetworkWake();worker.shutdown();new Thread(()->{try{if(worker.awaitTermination(30,TimeUnit.SECONDS)){health.setRecordingEnabled(false);health.close();}}catch(InterruptedException ex){Thread.currentThread().interrupt();}},"caregiver-store-close").start();super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
