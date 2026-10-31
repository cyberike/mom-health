package org.momhealth.gateway.data;
import java.util.*;
import org.json.*;
/** Parsed successful HTTP response; server contract promises commit before this response. */
public final class BatchAck {
    public final String batchId;
    public final Set<String> committedIds;
    public final Map<String,String> rejected;
    private final int requestedCount;
    private BatchAck(String id,Set<String> ok,Map<String,String> bad,int requestedCount) {
        batchId=id; committedIds=Collections.unmodifiableSet(ok); rejected=Collections.unmodifiableMap(bad); this.requestedCount=requestedCount;
    }
    /** True only when every requested item has an explicit accepted or duplicate acknowledgment. */
    public boolean allCommitted() { return committedIds.size()==requestedCount; }
    public static BatchAck parse(UploadBatch batch,String body) throws JSONException {
        JSONObject j=new JSONObject(body);
        if(!batch.batchId.equals(j.getString("batch_id"))) throw new JSONException("Batch mismatch");
        Set<String> requested=new HashSet<>(),seen=new HashSet<>(),ok=new HashSet<>(); Map<String,String> bad=new HashMap<>();
        for(HealthSample s:batch.samples) requested.add(s.sampleId);
        for(String key:new String[]{"accepted_ids","duplicate_ids"}) {
            JSONArray a=j.getJSONArray(key);
            for(int i=0;i<a.length();i++) { Object v=a.get(i); if(!(v instanceof String)) throw new JSONException("Invalid acknowledgment ID");
                String id=(String)v; check(id,requested,seen); ok.add(id); }
        }
        JSONArray a=j.getJSONArray("rejected");
        for(int i=0;i<a.length();i++) { JSONObject r=a.getJSONObject(i); String id=r.getString("sample_id"); check(id,requested,seen);
            Object reason=r.get("reason"); if(!(reason instanceof String) || ((String)reason).length()>256) throw new JSONException("Invalid rejection reason"); bad.put(id,(String)reason); }
        // Missing entries are deliberately left pending; never infer acceptance from HTTP success.
        return new BatchAck(batch.batchId,ok,bad,requested.size());
    }
    private static void check(String id,Set<String> requested,Set<String> seen) throws JSONException {
        if(!requested.contains(id)||!seen.add(id)) throw new JSONException("Unexpected or repeated acknowledgment ID");
    }
}
