package org.momhealth.gateway.automation;

import java.util.*;

/** Explicit caregiver policy, with no implicit cadence or consent defaults. */
public final class AutomationConfig {
    public static final long MIN_INTERVAL_MS=46000, MAX_INTERVAL_MS=86400000;
    public final String deviceId,model;
    public final long intervalMs,uploadIntervalMs;
    public final boolean acquisitionConsent,recordingConsent,uploadConsent;
    public final int maxSessionsPerHour,maxConnectAttempts;
    public AutomationConfig(String deviceId,String model,long intervalMs,boolean acquisitionConsent,
            boolean recordingConsent,boolean uploadConsent,int maxSessionsPerHour,int maxConnectAttempts,long uploadIntervalMs){
        if(deviceId==null || !deviceId.matches("[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"))throw new IllegalArgumentException("Opaque device UUID required");
        if(!"V1".equals(model)&&!"V8".equals(model))throw new IllegalArgumentException("Unvalidated model unavailable");
        if(intervalMs<MIN_INTERVAL_MS||intervalMs>MAX_INTERVAL_MS)throw new IllegalArgumentException("Explicit interval must be 46 seconds to 24 hours");
        if(maxSessionsPerHour<1||maxSessionsPerHour>60)throw new IllegalArgumentException("Session budget must be 1..60 per hour");
        if(maxConnectAttempts<1||maxConnectAttempts>6)throw new IllegalArgumentException("Connection budget must be 1..6 attempts");
        if(uploadIntervalMs<60000||uploadIntervalMs>MAX_INTERVAL_MS)throw new IllegalArgumentException("Explicit upload interval must be 1 minute to 24 hours");
        this.deviceId=UUID.fromString(deviceId).toString();this.model=model;this.intervalMs=intervalMs;
        this.acquisitionConsent=acquisitionConsent;this.recordingConsent=recordingConsent;this.uploadConsent=uploadConsent;
        this.maxSessionsPerHour=maxSessionsPerHour;this.maxConnectAttempts=maxConnectAttempts;this.uploadIntervalMs=uploadIntervalMs;
    }
    /** Persist only with explicit caregiver consent using the platform's private store. */
    public Map<String,String> toMap(){
        Map<String,String> m=new LinkedHashMap<>();m.put("version","1");m.put("deviceId",deviceId);m.put("model",model);
        m.put("intervalMs",Long.toString(intervalMs));m.put("uploadIntervalMs",Long.toString(uploadIntervalMs));
        m.put("acquisitionConsent",Boolean.toString(acquisitionConsent));m.put("recordingConsent",Boolean.toString(recordingConsent));m.put("uploadConsent",Boolean.toString(uploadConsent));
        m.put("maxSessionsPerHour",Integer.toString(maxSessionsPerHour));m.put("maxConnectAttempts",Integer.toString(maxConnectAttempts));return Collections.unmodifiableMap(m);
    }
    public static AutomationConfig fromMap(Map<String,String> m){
        Set<String> keys=new HashSet<>(Arrays.asList("version","deviceId","model","intervalMs","uploadIntervalMs","acquisitionConsent","recordingConsent","uploadConsent","maxSessionsPerHour","maxConnectAttempts"));
        if(m==null||!keys.equals(m.keySet())||!"1".equals(m.get("version")))throw new IllegalArgumentException("Unsupported/incomplete configuration");
        try{return new AutomationConfig(m.get("deviceId"),m.get("model"),Long.parseLong(m.get("intervalMs")),bool(m.get("acquisitionConsent")),bool(m.get("recordingConsent")),bool(m.get("uploadConsent")),Integer.parseInt(m.get("maxSessionsPerHour")),Integer.parseInt(m.get("maxConnectAttempts")),Long.parseLong(m.get("uploadIntervalMs")));}
        catch(RuntimeException e){throw new IllegalArgumentException("Invalid saved configuration",e);}
    }
    private static boolean bool(String s){if(!"true".equals(s)&&!"false".equals(s))throw new IllegalArgumentException("Explicit boolean required");return Boolean.parseBoolean(s);}
}
