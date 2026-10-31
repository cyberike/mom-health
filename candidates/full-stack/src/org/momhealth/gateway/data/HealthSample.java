package org.momhealth.gateway.data;

import java.time.OffsetDateTime;
import java.util.UUID;
import org.json.JSONException;
import org.json.JSONObject;

/** An explicitly supplied observation; receipt time is never substituted for watch time. */
public final class HealthSample {
    public final String sampleId, deviceId, sessionId, model, source, receivedAt, measuredAt, missingReason;
    public final Integer heartRateBpm;
    public final boolean synthetic;
    public HealthSample(String sampleId, String deviceId, String sessionId, String model,
            String source, String receivedAt, String measuredAt, Integer heartRateBpm,
            String missingReason, boolean synthetic) {
        this.sampleId = uuid(sampleId); this.deviceId = uuid(deviceId); this.sessionId = uuid(sessionId);
        if (!oneOf(model,"V1","V4","V8") || !oneOf(source,"live","history")) throw new IllegalArgumentException("Unsupported provenance");
        if (heartRateBpm != null && (heartRateBpm < 20 || heartRateBpm > 250)) throw new IllegalArgumentException("HR outside contract range");
        if (heartRateBpm == null && !oneOf(missingReason,"no_result","unsupported","not_requested","unknown")) throw new IllegalArgumentException("Missing value needs reason");
        if (heartRateBpm != null && missingReason != null) throw new IllegalArgumentException("Value cannot have missing reason");
        if (!synthetic && heartRateBpm != null && (!"live".equals(source) || !oneOf(model,"V1","V8"))) throw new IllegalArgumentException("Real HR is supported only for live V1/V8 observations");
        this.model=model; this.source=source; this.receivedAt=time(receivedAt);
        this.measuredAt=measuredAt==null?null:time(measuredAt); this.heartRateBpm=heartRateBpm;
        this.missingReason=missingReason; this.synthetic=synthetic;
    }
    private static String uuid(String s) {
        if (s==null || !s.matches("[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")) throw new IllegalArgumentException("Expected opaque UUID");
        return UUID.fromString(s).toString();
    }
    private static String time(String s) {
        if (s==null || s.length()>64) throw new IllegalArgumentException("Expected timestamp with timezone");
        try {
            OffsetDateTime parsed=OffsetDateTime.parse(s);
            int localYear=parsed.getYear();
            int utcYear=parsed.withOffsetSameInstant(java.time.ZoneOffset.UTC).getYear();
            if(localYear<1 || localYear>9999 || utcYear<1 || utcYear>9999) throw new IllegalArgumentException("Timestamp year must be 1..9999, including UTC normalization");
            return parsed.toInstant().toString();
        }
        catch (RuntimeException ex) { throw new IllegalArgumentException("Expected timestamp with timezone",ex); }
    }
    private static boolean oneOf(String v,String... values) { for(String s:values) if(s.equals(v)) return true; return false; }
    public JSONObject toJson() {
        try {
            JSONObject j=new JSONObject(); j.put("sample_id",sampleId); j.put("device_id",deviceId); j.put("session_id",sessionId);
            j.put("model",model); j.put("source",source); j.put("received_at",receivedAt);
            j.put("measured_at",measuredAt==null?JSONObject.NULL:measuredAt);
            j.put("heart_rate_bpm",heartRateBpm==null?JSONObject.NULL:heartRateBpm);
            j.put("missing_reason",missingReason==null?JSONObject.NULL:missingReason); j.put("synthetic",synthetic); return j;
        } catch(JSONException ex) { throw new IllegalStateException(ex); }
    }
    static HealthSample fromJson(String payload) {
        try { JSONObject j=new JSONObject(payload); return new HealthSample(j.getString("sample_id"),j.getString("device_id"),j.getString("session_id"),
            j.getString("model"),j.getString("source"),j.getString("received_at"),j.isNull("measured_at")?null:j.getString("measured_at"),
            j.isNull("heart_rate_bpm")?null:j.getInt("heart_rate_bpm"),j.isNull("missing_reason")?null:j.getString("missing_reason"),j.getBoolean("synthetic"));
        } catch(JSONException ex) { throw new IllegalArgumentException("Invalid stored sample",ex); }
    }
}
