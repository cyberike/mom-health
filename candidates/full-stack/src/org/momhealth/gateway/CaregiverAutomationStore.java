package org.momhealth.gateway;

import android.content.Context;
import android.content.SharedPreferences;
import org.momhealth.gateway.automation.AutomationConfig;
import org.momhealth.gateway.data.BatchUploader;
import java.util.ArrayList;
import java.util.List;

/** App-private consent/configuration, not a credential or health-value store. */
public final class CaregiverAutomationStore {
    private final SharedPreferences prefs;
    public CaregiverAutomationStore(Context c){prefs=c.getSharedPreferences("caregiver-automation-v1",Context.MODE_PRIVATE);}
    public static final class Settings {
        public final RememberedWatch watch;
        public final long intervalMs,uploadIntervalMs;
        public final int hourlyLimit,connectAttempts;
        public final boolean recording,upload,restore,rememberToken; public final int uploadedRetentionLimit;
        public final String endpoint;
        public Settings(RememberedWatch watch,long intervalMs,int hourlyLimit,int connectAttempts,boolean recording,boolean upload,long uploadIntervalMs,String endpoint,boolean restore,boolean rememberToken,int uploadedRetentionLimit){
            if(watch==null)throw new IllegalArgumentException("Remember a known watch first");
            this.watch=watch;this.intervalMs=intervalMs;this.hourlyLimit=hourlyLimit;this.connectAttempts=connectAttempts;this.recording=recording;this.upload=upload;this.uploadIntervalMs=uploadIntervalMs;this.endpoint=endpoint==null?"":endpoint.trim();this.restore=restore;this.rememberToken=rememberToken;this.uploadedRetentionLimit=uploadedRetentionLimit; if(uploadedRetentionLimit!=0&&(uploadedRetentionLimit<100||uploadedRetentionLimit>10000))throw new IllegalArgumentException("Uploaded retention must be 100..10000 or disabled"); if(rememberToken&&!upload)throw new IllegalArgumentException("Remembering a token requires upload consent");
            policy(upload);if(upload){if(!recording)throw new IllegalArgumentException("Upload requires recording consent");new BatchUploader(this.endpoint,false);}
        }
        public AutomationConfig policy(boolean allowUpload){return new AutomationConfig(watch.deviceId,watch.model,intervalMs,true,recording,upload&&allowUpload,hourlyLimit,connectAttempts,uploadIntervalMs);}
    }
    public Settings read(){
        if(!prefs.contains("version"))return null;
        if(prefs.getInt("version",0)!=1)throw new IllegalStateException("Unsupported automation settings version");
        RememberedWatch w=new RememberedWatch(prefs.getString("model",null),prefs.getString("address",null),prefs.getString("label",null),prefs.getString("deviceId",null));
        return new Settings(w,prefs.getLong("interval",0),prefs.getInt("hourly",0),prefs.getInt("attempts",0),prefs.getBoolean("recording",false),prefs.getBoolean("upload",false),prefs.getLong("uploadInterval",0),prefs.getString("endpoint",""),prefs.getBoolean("restore",false),prefs.getBoolean("rememberToken",false),prefs.getInt("uploadedRetention",0));
    }
    /** Saving configuration never grants ongoing operation. */
    public boolean save(Settings s){return prefs.edit().putInt("version",1).putString("model",s.watch.model).putString("address",s.watch.address).putString("label",s.watch.label).putString("deviceId",s.watch.deviceId).putLong("interval",s.intervalMs).putInt("hourly",s.hourlyLimit).putInt("attempts",s.connectAttempts).putBoolean("recording",s.recording).putBoolean("upload",s.upload).putLong("uploadInterval",s.uploadIntervalMs).putString("endpoint",s.endpoint).putBoolean("restore",s.restore).putBoolean("rememberToken",s.rememberToken).putInt("uploadedRetention",s.uploadedRetentionLimit).commit();}
    public boolean enabled(){return prefs.getBoolean("enabled",false);}
    public boolean shutdownUnconfirmed(){return prefs.getBoolean("measurementOutstanding",false);}
    public boolean confirmCleanup(){return prefs.edit().putBoolean("measurementOutstanding",false).commit();}
    public boolean bootUploadPaused(){return prefs.getBoolean("bootUploadPaused",false);}
    public boolean pauseBootUpload(boolean paused){return prefs.edit().putBoolean("bootUploadPaused",paused).commit();}
    public boolean setEnabled(boolean enabled){return prefs.edit().putBoolean("enabled",enabled).commit();}
    public String status(){return prefs.getString("status","Caregiver automation is off. No service has been started.");}
    public void status(String value){String bounded=value.length()>500?value.substring(0,500):value;if(!bounded.equals(status()))prefs.edit().putString("status",bounded).apply();}
    /** Persist only request times so restart cannot bypass the configured cadence/hourly budget. */
    private List<Long> starts(long now){
        List<Long> values=new ArrayList<>();String raw=prefs.getString("starts","");
        if(raw.length()>2000)throw new IllegalStateException("Request budget metadata invalid");
        if(!raw.isEmpty())for(String part:raw.split(",")){long at=Long.parseLong(part);if(at<0)throw new IllegalStateException("Invalid request time");if(at>now||now-at<3600000L)values.add(at);}
        if(values.size()>60)throw new IllegalStateException("Request budget metadata invalid");return values;
    }
    public synchronized long delayUntilAllowed(Settings settings,long now){
        if(now<0)throw new IllegalArgumentException("Invalid wall clock");List<Long> values=starts(now);long wait=0;
        long last=prefs.getLong("lastStart",0);if(last>now)return 3600000L; // Clock moved backwards: do not burst.
        if(last>0)wait=Math.max(0,settings.intervalMs-(now-last));
        if(values.size()>=settings.hourlyLimit){long oldest=values.get(0);wait=Math.max(wait,oldest>now?3600000L:Math.max(0,3600000L-(now-oldest)));}
        return wait;
    }
    public synchronized boolean markStarted(Settings settings,long now){
        if(delayUntilAllowed(settings,now)>0)return false;List<Long> values=starts(now);values.add(now);StringBuilder out=new StringBuilder();for(long value:values){if(out.length()>0)out.append(',');out.append(value);}
        return prefs.edit().putString("starts",out.toString()).putLong("lastStart",now).putBoolean("measurementOutstanding",true).commit();
    }
}

