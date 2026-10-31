package org.momhealth.gateway.data;

import java.util.Arrays;
import java.util.Collections;
import org.json.JSONException;
import org.json.JSONObject;

/** Synthetic protocol tests; these do not execute Android SQLite or networking. */
public final class AckRetryRegressionTests {
    private static int checks;
    private static void check(boolean value, String message) {
        checks++;
        if (!value) throw new AssertionError(message);
    }
    private static HealthSample sample(String id) {
        return new HealthSample(id, "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003", "V8", "live",
                "2026-10-29T04:00:00Z", null, 75, null, true);
    }
    private static String reply(String batchId, String accepted, String duplicates, String rejected) {
        return "{\"batch_id\":\"" + batchId + "\",\"accepted_ids\":" + accepted +
                ",\"duplicate_ids\":" + duplicates + ",\"rejected\":" + rejected + "}";
    }
    private static void invalid(UploadBatch batch, String body, String message) throws Exception {
        checks++;
        try { BatchAck.parse(batch, body); }
        catch (JSONException expected) { return; }
        throw new AssertionError(message);
    }
    public static void main(String[] args) throws Exception {
        String a="00000000-0000-0000-0000-000000000001";
        String b="00000000-0000-0000-0000-000000000004";
        String unrelated="00000000-0000-0000-0000-000000000005";
        HealthSample first=sample(a), second=sample(b);
        UploadBatch original=new UploadBatch(Arrays.asList(first,second));
        BatchAck partial=BatchAck.parse(original,reply(original.batchId,"[\""+a+"\"]","[]","[]"));
        check(partial.committedIds.contains(a),"accepted ID must be committed");
        check(!partial.committedIds.contains(b) && !partial.allCommitted(),"omitted ID remains uncommitted");
        check(partial.rejected.isEmpty(),"omission is distinct from explicit rejection");
        UploadBatch retry=new UploadBatch(Collections.singletonList(second));
        check(!original.batchId.equals(retry.batchId),"retry uses a fresh batch identifier");
        JSONObject retried=new JSONObject(retry.toJson());
        check(b.equals(retried.getJSONArray("samples").getJSONObject(0).getString("sample_id")),"retry retains sample identity");
        BatchAck duplicate=BatchAck.parse(retry,reply(retry.batchId,"[]","[\""+b+"\"]","[]"));
        check(duplicate.allCommitted() && duplicate.committedIds.contains(b),"duplicate acknowledgement commits retried sample");
        invalid(retry,reply(original.batchId,"[]","[\""+b+"\"]","[]"),"stale batch response rejected");
        invalid(retry,reply(retry.batchId,"[\""+unrelated+"\"]","[]","[]"),"unrequested ID rejected");
        invalid(retry,reply(retry.batchId,"[\""+b+"\"]","[]","[{\"sample_id\":\""+b+"\",\"reason\":\"collision\"}]"),"repeated ID across response classes rejected");
        invalid(retry,reply(retry.batchId,"[]","[]","[{\"sample_id\":\""+b+"\",\"reason\":null}]"),"non-string reason rejected");
        check(!UploadOutcome.incomplete(duplicate),"fully acknowledged retry is not incomplete");
        check(UploadOutcome.incomplete(partial),"partial response remains visibly incomplete");
        System.out.println("PASS: "+checks+" synthetic acknowledgment/retry checks; no Android storage, network or device.");
    }
}
