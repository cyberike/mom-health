package org.momhealth.gateway;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.*;
import android.graphics.Color;
import android.text.InputType;
import android.widget.*;
import java.util.ArrayList;
import org.momhealth.gateway.data.BatchUploader;

/** Caregiver-only setup. No automatic permission acceptance, measurement or service startup. */
public final class CaregiverAutomationActivity extends Activity {
    private CaregiverAutomationStore store;private RememberedWatch watch;private TextView status,selected,automaticReading;
    private EditText interval,hourly,attempts,uploadInterval,endpoint,token,retention;
    private CheckBox acquisition,recording,upload,restore,rememberToken,prune;
    private boolean visible;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final Runnable refresh=new Runnable(){public void run(){if(visible){status.setText(store.status());CaregiverService.SavedReading reading=CaregiverService.lastSavedReading();String at=reading==null?null:java.text.DateFormat.getDateTimeInstance().format(new java.util.Date(reading.receivedAt));automaticReading.setText(CaregiverStatusDisplay.reading(reading==null?null:reading.bpm,at));handler.postDelayed(this,2000);}}};
    @Override public void onCreate(Bundle saved){super.onCreate(saved);store=new CaregiverAutomationStore(this);
        ScrollView scroll=new ScrollView(this);LinearLayout page=new LinearLayout(this);page.setOrientation(LinearLayout.VERTICAL);int pad=Math.round(20*getResources().getDisplayMetrics().density);page.setPadding(pad,pad,pad,pad);page.setBackgroundColor(0xff10121b);scroll.addView(page);setContentView(scroll);
        scroll.setOnApplyWindowInsetsListener((view,insets)->{android.graphics.Insets bars=insets.getInsets(android.view.WindowInsets.Type.systemBars()|android.view.WindowInsets.Type.displayCutout());view.setPadding(bars.left,bars.top,bars.right,bars.bottom);return insets;});
        text(page,"Caregiver setup",30);text(page,"Mom's routine is wearing and charging. A caregiver chooses this schedule and manages permissions. This candidate is not a medical monitor and has not passed unattended device acceptance.",17);
        selected=text(page,"No remembered watch",18);status=text(page,store.status(),17);automaticReading=text(page,CaregiverStatusDisplay.reading(null,null),17);
        acquisition=check(page,"I consent to scheduled, bounded watch HR requests");
        interval=field(page,"Minutes between request starts (required; 1–1440)",true,false);
        hourly=field(page,"Maximum requests per hour (required; 1–60)",true,false);
        attempts=field(page,"Connection attempts before caregiver attention (required; 1–6)",true,false);
        recording=check(page,"Save received readings locally while this service runs");
        restore=check(page,"Restore this consented operation after restart/unlocked boot");
        text(page,"Each request is capped at 30 seconds. No exact cadence is guaranteed during Doze, radio loss, low battery or OS restrictions. Reconnection is bounded. Unconfirmed sensor shutdown requires caregiver review.",16);
        upload=check(page,"Upload saved readings and technical status to my configured server");
        uploadInterval=field(page,"Minutes between upload attempts (required if enabled)",true,false);
        endpoint=field(page,"HTTPS endpoint ending /v1/batches",false,false);
        token=field(page,"Access token; never shown or logged",false,true);
        rememberToken=check(page,"Remember this token using Android Keystore encryption");
        text(page,"A remembered token can be used without unlocking the phone after its first boot unlock. Backup is disabled. A caregiver can forget it locally; that does not revoke it at the server. Without remembering, upload pauses after process restart.",16);
        text(page,"After reboot, separately consented uploads use Android's constrained job scheduler: at least 15 minutes between attempts, possibly later under battery/network restrictions. No data-sync foreground service is started from boot.",16);
        prune=check(page,"Allow removing oldest server-acknowledged local records only");
        retention=field(page,"Acknowledged records to retain (100–10000)",true,false);
        text(page,"Pending readings are never automatically deleted. A full pending buffer stops recording and requests caregiver attention. No setting is enabled by installing this build.",16);
        action(page,"Review required permissions",this::reviewPermissions);
        action(page,"Save setup without starting",()->{try{if(CaregiverService.isRunning())throw new IllegalStateException("Stop automatic work before editing setup");CaregiverAutomationStore.Settings s=settings();if(!store.save(s))throw new IllegalStateException("Could not save setup");show("Setup saved. Nothing has started; token is saved only when you explicitly start with its opt-in.");}catch(RuntimeException ex){show(ex.getMessage());}});
        action(page,"Start consented automatic work",this::confirmStart);
        action(page,"Acknowledge an unconfirmed shutdown review",()->{if(CaregiverService.isRunning()){show("Stop automatic work before reviewing a shutdown.");return;}if(!store.shutdownUnconfirmed()){show("No unresolved saved shutdown marker.");return;}new AlertDialog.Builder(this).setTitle("Caregiver watch review required").setMessage("An earlier request lost confirmed cleanup. Inspect the watch and its behavior first. This acknowledgment does not prove sensor shutdown or start another request.").setNegativeButton("Not reviewed",null).setPositiveButton("I reviewed the watch",(d,w)->show(store.confirmCleanup()?"Review acknowledged. Nothing started; explicitly start only when ready.":"Review acknowledgment could not be saved.")).show();});
        action(page,"Stop automatic work",()->{store.setEnabled(false);CaregiverUploadJobService.cancel(this);if(CaregiverService.isRunning())startService(new Intent(this,CaregiverService.class).setAction(CaregiverService.STOP));else store.status("Automatic work is off.");show("Stop requested. Pending readings remain local.");});
        action(page,"Forget saved upload token",()->{if(CaregiverService.isRunning()){show("Stop automatic work before forgetting its credential.");return;}new AlertDialog.Builder(this).setTitle("Forget local token?").setMessage("This removes the encrypted token from this phone. It does not revoke server access elsewhere or delete readings.").setNegativeButton("Keep",null).setPositiveButton("Forget",(d,w)->show(new CaregiverCredentialVault(this).forget()?"Local token forgotten.":"Could not remove local token.")).show();});
        action(page,"Return to manual screen",this::finish);load();
    }
    private TextView text(LinearLayout box,String value,int size){TextView t=new TextView(this);t.setText(value);t.setTextSize(size);t.setTextColor(Color.WHITE);t.setPadding(0,10,0,10);box.addView(t);return t;}
    private CheckBox check(LinearLayout box,String value){CheckBox c=new CheckBox(this);c.setText(value);c.setTextColor(Color.WHITE);c.setTextSize(17);box.addView(c);return c;}
    private EditText field(LinearLayout box,String hint,boolean numeric,boolean secret){EditText e=new EditText(this);e.setHint(hint);e.setTextColor(Color.WHITE);e.setHintTextColor(0xffcbd0e1);e.setTextSize(17);e.setInputType(numeric?InputType.TYPE_CLASS_NUMBER:InputType.TYPE_CLASS_TEXT|(secret?InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_TEXT_VARIATION_URI));e.setSaveEnabled(false);box.addView(e,new LinearLayout.LayoutParams(-1,-2));return e;}
    private void action(LinearLayout box,String title,Runnable task){Button b=new Button(this);b.setText(title);b.setTextSize(17);b.setAllCaps(false);b.setMinHeight(Math.round(56*getResources().getDisplayMetrics().density));b.setOnClickListener(v->task.run());box.addView(b,new LinearLayout.LayoutParams(-1,-2));}
    private void show(String message){status.setText(message==null?"Operation unavailable":message);}
    private void load(){try{watch=new CaregiverSetupStore(this).read();CaregiverAutomationStore.Settings s=store.read();if(watch==null&&s!=null)watch=s.watch;if(watch!=null)selected.setText("Caregiver-selected model: "+watch.model+"\n"+watch.label);if(s!=null){interval.setText(Long.toString(s.intervalMs/60000));hourly.setText(Integer.toString(s.hourlyLimit));attempts.setText(Integer.toString(s.connectAttempts));recording.setChecked(s.recording);upload.setChecked(s.upload);uploadInterval.setText(Long.toString(s.uploadIntervalMs/60000));endpoint.setText(s.endpoint);restore.setChecked(s.restore);rememberToken.setChecked(s.rememberToken);prune.setChecked(s.uploadedRetentionLimit>0);if(s.uploadedRetentionLimit>0)retention.setText(Integer.toString(s.uploadedRetentionLimit));}}catch(RuntimeException ex){show("Saved setup is invalid. Return to the manual screen and remember your known watch.");}}
    private int number(EditText view,int min,int max,String label){try{int n=Integer.parseInt(view.getText().toString().trim());if(n<min||n>max)throw new NumberFormatException();return n;}catch(NumberFormatException ex){throw new IllegalArgumentException(label+" must be "+min+"–"+max);}}
    private CaregiverAutomationStore.Settings settings(){if(!acquisition.isChecked())throw new IllegalArgumentException("Explicit acquisition consent is required");if(watch==null)throw new IllegalArgumentException("Remember a known watch on the manual screen first");return new CaregiverAutomationStore.Settings(watch,number(interval,1,1440,"Measurement minutes")*60000L,number(hourly,1,60,"Hourly limit"),number(attempts,1,6,"Connection attempts"),recording.isChecked(),upload.isChecked(),upload.isChecked()?number(uploadInterval,1,1440,"Upload minutes")*60000L:60000L,endpoint.getText().toString().trim(),restore.isChecked(),rememberToken.isChecked(),prune.isChecked()?number(retention,100,10000,"Acknowledged retention"):0);}
    private void reviewPermissions(){ArrayList<String> needed=new ArrayList<>();if(checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED)needed.add(Manifest.permission.BLUETOOTH_CONNECT);if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)needed.add(Manifest.permission.POST_NOTIFICATIONS);if(needed.isEmpty()){show(CaregiverService.prerequisites(this)?"Required runtime permissions are granted. Nothing started.":"Notifications are disabled. Review notification settings yourself before enabling this service.");return;}requestPermissions(needed.toArray(new String[0]),81);}
    private void confirmStart(){
        if(!visible||CaregiverService.isRunning()){show("Automatic work is already running or starting. Stop it before changing setup.");return;}
        if(store.shutdownUnconfirmed()){show("Previous measurement cleanup was unconfirmed. Use the caregiver shutdown review before restarting.");return;}
        final CaregiverAutomationStore.Settings s;try{s=settings();}catch(RuntimeException ex){show(ex.getMessage());return;}
        if(!CaregiverService.prerequisites(this)){show("Review required Bluetooth and notification permissions first. Nothing started.");return;}
        new AlertDialog.Builder(this).setTitle("Start caregiver-selected operation?").setMessage("Model "+s.watch.model+". One bounded request every "+(s.intervalMs/60000)+" minutes, maximum "+s.hourlyLimit+" per hour. Local recording "+(s.recording?"on":"off")+"; upload "+(s.upload?"on":"off")+". A visible notification provides Stop. Actual timing/accuracy is unverified. Restore after restart: "+s.restore+".")
            .setNegativeButton("Cancel",null).setPositiveButton("Start",(d,w)->start(s)).show();
    }
    private void start(CaregiverAutomationStore.Settings s){
        if(!visible||!CaregiverService.prerequisites(this)||!CaregiverService.reserveStart()){show("Start cancelled or operation already running.");return;}
        try{String secret=token.getText().toString();CaregiverCredentialVault vault=new CaregiverCredentialVault(this);
            if(s.upload){if(secret.isEmpty()&&s.rememberToken)secret=vault.read(s.endpoint,s.watch.deviceId);if(secret==null||secret.isEmpty())throw new IllegalArgumentException("Upload needs an explicitly provided valid token");new BatchUploader(s.endpoint,false,secret);if(s.rememberToken)vault.save(s.endpoint,s.watch.deviceId,secret);}
            if(!store.save(s)||!store.setEnabled(true)||!store.pauseBootUpload(false))throw new IllegalStateException("Consent could not be saved");if(!CaregiverUploadJobService.schedule(this,s))throw new IllegalStateException("Upload job could not be scheduled");
            Intent intent=new Intent(this,CaregiverService.class).setAction(CaregiverService.START);if(s.upload)intent.putExtra(CaregiverService.TOKEN,secret);startForegroundService(intent);token.setText("");show("Foreground-service start requested. Check its visible notification and status.");
        }catch(Exception ex){store.setEnabled(false);CaregiverUploadJobService.cancel(this);CaregiverService.releaseReservation();show("Could not start. Review setup, permissions or credential; no token is shown in this error.");}
    }
    @Override public void onRequestPermissionsResult(int code,String[] names,int[] results){super.onRequestPermissionsResult(code,names,results);show("Permission review finished. Tap Start only if you want to enable operation.");}
    @Override protected void onResume(){super.onResume();visible=true;handler.post(refresh);}
    @Override protected void onPause(){visible=false;token.setText("");handler.removeCallbacks(refresh);super.onPause();}
}
