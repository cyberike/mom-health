package org.momhealth.gateway.data;

import android.content.Context;
import java.util.*;
import org.json.*;

/** NOT EXECUTED. Future explicit invocation only in a disposable candidate Android context. */
public final class DataComponentChecks {
    private static void check(boolean condition) { if(!condition) throw new AssertionError(); }
    private static HealthSample sample(String id,Integer hr,String reason) {
        return new HealthSample(id,"00000000-0000-0000-0000-000000000002","00000000-0000-0000-0000-000000000003","V8","live","2026-10-22T12:00:00Z",null,hr,reason,true);
    }
    private static void invalid(Runnable action) { try { action.run(); } catch(IllegalArgumentException expected) { return; } throw new AssertionError("Expected rejection"); }
    private static HealthSample provenance(String model,String source,String time,Integer hr,boolean synthetic) {
        return new HealthSample("00000000-0000-0000-0000-000000000001","00000000-0000-0000-0000-000000000002","00000000-0000-0000-0000-000000000003",model,source,time,null,hr,hr==null?"unsupported":null,synthetic);
    }
    public static void modelAndAcknowledgmentChecks() throws Exception {
        String id="00000000-0000-0000-0000-000000000001";
        HealthSample s=sample(id,82,null); check(s.measuredAt==null); check(s.toJson().isNull("measured_at"));
        invalid(()->sample(id,0,null)); invalid(()->sample(id,251,null)); invalid(()->sample(id,null,null)); invalid(()->sample(id,82,"unknown"));
        invalid(()->sample("not-an-id",82,null)); check(sample(id,null,"no_result").heartRateBpm==null);
        invalid(()->provenance("V4","live","2026-10-22T12:00:00Z",82,false));
        invalid(()->provenance("V8","history","2026-10-22T12:00:00Z",82,false));
        check(provenance("V4","history","2026-10-22T12:00:00Z",null,false).heartRateBpm==null);
        check(provenance("V4","history","2026-10-22T12:00:00Z",82,true).synthetic);
        check(!provenance("V1","live","2026-10-22T12:00:00Z",82,false).synthetic);
        invalid(()->provenance("V8","live","0000-01-01T00:00:00Z",82,true));
        invalid(()->provenance("V8","live","+10000-01-01T00:00:00Z",82,true));
        invalid(()->provenance("V8","live","0001-01-01T00:00:00+01:00",82,true));
        invalid(()->provenance("V8","live","9999-12-31T23:59:59-01:00",82,true));
        check(provenance("V8","live","0001-01-01T00:00:00Z",82,true).receivedAt.startsWith("0001"));
        check(provenance("V8","live","9999-12-31T23:59:59Z",82,true).receivedAt.startsWith("9999"));
        invalid(()->new UploadBatch(Arrays.asList(s,s)));
        UploadBatch b=new UploadBatch(Collections.singletonList(s));
        String empty="{\"batch_id\":\""+b.batchId+"\",\"accepted_ids\":[],\"duplicate_ids\":[],\"rejected\":[]}";
        check(BatchAck.parse(b,empty).committedIds.isEmpty());
        JSONObject ack=new JSONObject(empty); ack.getJSONArray("accepted_ids").put(id);
        check(BatchAck.parse(b,ack.toString()).committedIds.contains(id));
        ack.getJSONArray("duplicate_ids").put(id);
        try { BatchAck.parse(b,ack.toString());throw new AssertionError(); } catch(JSONException expected) { }
        invalid(()->new BatchUploader("http://example.com/v1/batches",true));
        invalid(()->new BatchUploader("https://user:secret@example.com/v1/batches",false));
        invalid(()->new BatchUploader("http://127.0.0.1/v1/batches",false));
        invalid(()->new BatchUploader("http://127.0.0.1/v1/batches",true,"secret"));
        invalid(()->new BatchUploader("https://example.com/v1/batches",false,"bad\r\nvalue"));
        invalid(()->new BatchUploader("https://example.com/v1/batches",false,"short-token"));
        BatchUploader uploader=new BatchUploader("http://127.0.0.1/v1/batches",true);
        uploader.cancel(); check(uploader.isCancelled()); // No network call.
    }
    public static void isolatedStoreChecks(Context disposableContext) throws Exception {
        // Destructive to this context's candidate DB: NEVER use a real user's application context.
        HealthStore store=new HealthStore(disposableContext);
        try {
            store.clearAll(); HealthSample first=sample("00000000-0000-0000-0000-000000000001",82,null);
            try { store.append(first); throw new AssertionError(); } catch(IllegalStateException expected) { }
            store.setRecordingEnabled(true); check(store.append(first)); check(!store.append(first));
            invalid(()->store.append(sample(first.sampleId,83,null)));
            check(store.status().pending==1); check(store.history(1,0).get(0).sample.heartRateBpm==82);
            UploadBatch b=store.pendingBatch(100); store.beginAttempt(b);
            BatchAck partial=BatchAck.parse(b,"{\"batch_id\":\""+b.batchId+"\",\"accepted_ids\":[],\"duplicate_ids\":[],\"rejected\":[]}");
            store.acknowledge(b,partial); check(store.status().pending==1); check(store.status().failed==1);
            BatchAck ok=BatchAck.parse(b,"{\"batch_id\":\""+b.batchId+"\",\"accepted_ids\":[\""+first.sampleId+"\"],\"duplicate_ids\":[],\"rejected\":[]}");
            store.acknowledge(b,ok); check(store.status().uploaded==1); check(store.status().pending==0);
            store.deleteSample(first.sampleId); check(store.history(100,0).isEmpty());
            invalid(()->store.history(101,0)); invalid(()->store.pendingBatch(0));
        } finally { store.close(); }
    }
}
