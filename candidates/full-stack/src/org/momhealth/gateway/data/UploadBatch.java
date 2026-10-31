package org.momhealth.gateway.data;
import java.util.*;
import org.json.*;
public final class UploadBatch {
    public final String batchId;
    public final List<HealthSample> samples;
    public UploadBatch(List<HealthSample> values) {
        if(values==null || values.size()>100) throw new IllegalArgumentException("Batch maximum is 100");
        Set<String> ids=new HashSet<>();
        for(HealthSample s:values) if(s==null || !ids.add(s.sampleId)) throw new IllegalArgumentException("Duplicate or null sample");
        samples=Collections.unmodifiableList(new ArrayList<>(values)); batchId=UUID.randomUUID().toString();
    }
    public String toJson() {
        try { JSONObject j=new JSONObject(); j.put("schema_version",1); j.put("batch_id",batchId);
            JSONArray a=new JSONArray(); for(HealthSample s:samples) a.put(s.toJson()); j.put("samples",a); return j.toString();
        } catch(JSONException ex) { throw new IllegalStateException(ex); }
    }
}
