package org.momhealth.gateway;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Handler;
import android.os.Looper;
import android.widget.*;
import android.text.InputType;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import org.momhealth.gateway.data.*;

/** Candidate-only UI composition. No database/network operation until an explicit action. */
final class CandidateDataPanel {
    private final Activity activity;
    private final HealthStore store;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final TextView status;
    private final CheckBox recording;
    private final EditText endpoint,token;
    private final Button upload;
    private final Map<String,String> deviceIds=new HashMap<>(); // In-memory only; never persist BLE identity.
    private String deviceId,sessionId,model;
    private volatile boolean foreground;
    private boolean busy,destroyed;
    private volatile long generation;
    private volatile BatchUploader uploader;
    private Future<?> operation;

    CandidateDataPanel(Activity activity,LinearLayout parent) {
        this.activity=activity;store=new HealthStore(activity);
        label(parent,"CANDIDATE DATA CONTROLS",20);
        label(parent,"Manual data controls. Saving starts off here. Optional automatic operation requires separate caregiver setup and consent. Accuracy is unverified.",16);
        recording=new CheckBox(activity);recording.setText("Save future readings on this phone");recording.setTextColor(0xfff5f6fa);recording.setTextSize(18);parent.addView(recording);
        recording.setOnCheckedChangeListener((button,enabled)->{
            store.setRecordingEnabled(enabled);
            statusText(enabled?"Local recording enabled for this visit. Existing readings are not backfilled.":"Local recording off. Saved records remain until you explicitly clear them.");
        });
        status=label(parent,"Local storage has not been opened. Upload destination is not configured.",16);
        action(parent,"Show local history and sync status",()->readHistory(0));
        action(parent,"Watch history availability",()->new AlertDialog.Builder(activity).setTitle("Watch history is not enabled")
            .setMessage("V1, V4 and V8 history formats have offline parsers, but safe completion and timestamp interpretation still need validation. This control sends no command. Local history below contains only readings you explicitly saved on this phone.")
            .setPositiveButton("Close",null).show());
        endpoint=field(parent,"HTTPS batch endpoint (blank by default)",false);
        token=field(parent,"Optional access token (memory only)",true);
        upload=action(parent,"Upload one pending batch",this::confirmUpload);
        action(parent,"Clear locally saved readings",()->new AlertDialog.Builder(activity).setTitle("Clear local candidate data?")
            .setMessage("This deletes only this candidate app's saved readings. It does not delete records already sent to a server. Cancel any upload first.")
            .setNegativeButton("Keep",null).setPositiveButton("Clear local data",(d,w)->{
                if(CaregiverService.isRunning()){statusText("Stop automatic caregiver work before clearing local data.");return;}
                if(busy){statusText("Upload is in progress. Leave this screen to cancel, then return before clearing.");return;}
                submit(()->{store.clearAll();return "Local saved readings cleared.";});
            }).show());
        action(parent,"How to use this candidate",()->new AlertDialog.Builder(activity).setTitle("Connect, measure, reconnect")
            .setMessage("Choose your physical model, find your known watch, and confirm it. Tap Measure for one bounded 30-second request; Stop ends it early. Leaving this screen disconnects; return and tap Reconnect, then Measure again. Old readings are never a new measurement.\n\nThese manual controls start with saving off each visit. Manual upload is a separate explicit action. Optional caregiver automatic operation has separate recording, upload and restart consent, a visible notification and Stop control. There are no clinical alerts, medical interpretations or verified accuracy. Watch history and V4 measurements are unavailable. No sleep, blood pressure or glucose values are inferred.")
            .setPositiveButton("Close",null).show());
    }
    private TextView label(LinearLayout box,String s,int size){TextView t=new TextView(activity);t.setText(s);t.setTextSize(size);t.setTextColor(0xfff5f6fa);t.setPadding(0,12,0,12);box.addView(t);return t;}
    private Button action(LinearLayout box,String s,Runnable r){Button b=new Button(activity);b.setText(s);b.setTextSize(18);b.setAllCaps(false);b.setMinHeight(56);box.addView(b,new LinearLayout.LayoutParams(-1,-2));b.setOnClickListener(v->r.run());return b;}
    private EditText field(LinearLayout box,String hint,boolean secret){EditText e=new EditText(activity);e.setHint(hint);e.setTextColor(0xfff5f6fa);e.setHintTextColor(0xffccd0df);e.setTextSize(16);e.setSingleLine(false);e.setInputType(secret?InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);e.setSaveEnabled(false);box.addView(e,new LinearLayout.LayoutParams(-1,-2));return e;}
    private void statusText(String s){if(!destroyed)status.setText(s);}
    void foreground(){foreground=true;boolean automatic=CaregiverService.isRunning();recording.setEnabled(!automatic);upload.setEnabled(!automatic);if(automatic)statusText("Automatic caregiver service owns saving and upload. Local history remains available.");}
    void select(String selectionKey,String model){if(!model.startsWith("V8")&&!model.startsWith("V1"))throw new IllegalArgumentException("Unsupported live model");if(deviceIds.size()>=100&&!deviceIds.containsKey(selectionKey))deviceIds.clear();deviceId=deviceIds.computeIfAbsent(selectionKey,k->UUID.randomUUID().toString());this.model=model.startsWith("V8")?"V8":"V1";sessionId=null;}
    void resetSelection(){deviceId=null;sessionId=null;model=null;}
    String selectedDeviceId(){return deviceId;}
    void selectRemembered(String selectionKey,String model,String rememberedId){select(selectionKey,model);deviceId=java.util.UUID.fromString(rememberedId).toString();deviceIds.put(selectionKey,deviceId);}
    void beginMeasurement(){sessionId=UUID.randomUUID().toString();}
    void reading(int bpm,long receivedAt){
        if(!foreground||!store.isRecordingEnabled()||deviceId==null||sessionId==null)return;
        final HealthSample sample;
        try{sample=new HealthSample(UUID.randomUUID().toString(),deviceId,sessionId,model,"live",Instant.ofEpochMilli(receivedAt).toString(),null,bpm,null,false);}
        catch(RuntimeException ex){statusText("Reading was not saved: invalid value or provenance.");return;}
        final long epoch=generation;
        worker.execute(()->{try{if(!foreground||epoch!=generation||!store.isRecordingEnabled())return;store.append(sample);main.post(()->{if(foreground&&epoch==generation)statusText("Reading saved locally. Upload only occurs when you request it.");});}
            catch(RuntimeException ex){main.post(()->{if(foreground&&epoch==generation)statusText("Reading was not saved: "+ex.getMessage());});}});
    }
    private interface Work {String run() throws Exception;}
    private void submit(Work task){
        if(!foreground||busy||destroyed)return;busy=true;upload.setEnabled(false);final long epoch=generation;
        operation=worker.submit(()->{if(!foreground||epoch!=generation||Thread.currentThread().isInterrupted())return;String outcome;try{outcome=task.run();}catch(Exception ex){outcome="Operation did not complete: "+ex.getClass().getSimpleName()+". Pending records are retained.";}
            final String result=outcome;main.post(()->{if(epoch!=generation)return;busy=false;upload.setEnabled(true);if(foreground)statusText(result);});});
    }
    private void readHistory(long before){
        if(!foreground||busy||destroyed)return;busy=true;upload.setEnabled(false);final long epoch=generation;
        operation=worker.submit(()->{if(!foreground||epoch!=generation||Thread.currentThread().isInterrupted())return;try{
            List<HealthStore.HistoryEntry> rows=store.history(20,before);HealthStore.Status s=store.status();
            StringBuilder text=new StringBuilder("Pending: "+s.pending+"; uploaded: "+s.uploaded+"; failed/pending: "+s.failed+"\n");
            for(HealthStore.HistoryEntry row:rows)text.append("\n").append(row.sample.receivedAt).append(" | ").append(row.sample.model).append(" | device ").append(row.sample.deviceId.substring(0,8)).append(" | ").append(row.sample.heartRateBpm==null?"No HR":row.sample.heartRateBpm+" bpm").append(row.uploaded?" | uploaded":" | pending");
            main.post(()->{if(epoch!=generation)return;busy=false;upload.setEnabled(true);if(!foreground)return;AlertDialog.Builder dialog=new AlertDialog.Builder(activity).setTitle("Saved local readings").setMessage(text.toString()).setPositiveButton("Close",null);
                if(rows.size()==20)dialog.setNeutralButton("Older",(d,w)->readHistory(rows.get(rows.size()-1).rowId));dialog.show();});
        }catch(RuntimeException ex){main.post(()->{if(epoch!=generation)return;busy=false;upload.setEnabled(true);if(foreground)statusText("Local history unavailable; records were not changed.");});}});
    }
    private void confirmUpload(){
        if(CaregiverService.isRunning()){statusText("Automatic caregiver service owns upload. Stop it before manual upload.");return;}
        if(!foreground||busy)return;
        final String url=endpoint.getText().toString().trim(),entered=token.getText().toString(),secret=entered.isEmpty()?null:entered;
        final BatchUploader next;try{next=new BatchUploader(url,false,secret);}catch(RuntimeException ex){statusText("Enter a valid HTTPS /v1/batches endpoint. No upload started.");return;}
        new AlertDialog.Builder(activity).setTitle("Send up to 100 pending readings?").setMessage("Destination: "+url+"\nIncludes saved heart rate, receipt times, and opaque device/session IDs. Only explicit server acknowledgments mark records uploaded. Cancellation may occur after the server commits; retry is idempotent. No automatic retry.")
            .setNegativeButton("Cancel",null).setPositiveButton("Upload once",(d,w)->{if(!foreground||busy)return;uploader=next;submit(()->{try{BatchAck ack=SyncCoordinator.uploadOnce(store,next);return ack==null?"No pending readings.":"Server acknowledged "+ack.committedIds.size()+" readings; rejected or unacknowledged readings remain pending.";}finally{if(uploader==next)uploader=null;}});}).show();
    }
    void leave(){foreground=false;generation++;sessionId=null;store.setRecordingEnabled(false);recording.setChecked(false);token.setText("");BatchUploader current=uploader;if(current!=null)current.cancel();if(operation!=null)operation.cancel(true);busy=false;upload.setEnabled(true);}
    void destroy(){leave();destroyed=true;worker.shutdown();/* Close after queued finite operations, without blocking the UI. */new Thread(()->{try{if(worker.awaitTermination(30,TimeUnit.SECONDS))store.close();}catch(InterruptedException ex){Thread.currentThread().interrupt();}},"candidate-store-close").start();}
}
