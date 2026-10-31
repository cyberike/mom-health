package org.momhealth.gateway.data;

import java.util.Arrays;
import org.json.JSONException;

/** Synthetic wire-response checks; no network, Android database, or device. */
public final class BatchAcknowledgmentTests {
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }
    private static HealthSample sample(String id) {
        return new HealthSample(id, "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003", "V8", "live",
                "2026-10-29T04:00:00Z", null, 75, null, true);
    }
    private static String response(UploadBatch batch, String accepted, String duplicate, String rejected) {
        return "{\"batch_id\":\"" + batch.batchId + "\",\"accepted_ids\":" + accepted +
                ",\"duplicate_ids\":" + duplicate + ",\"rejected\":" + rejected + "}";
    }
    public static void main(String[] args) throws Exception {
        String first="00000000-0000-0000-0000-000000000001", second="00000000-0000-0000-0000-000000000004";
        UploadBatch batch = new UploadBatch(Arrays.asList(sample(first), sample(second)));
        check(BatchAck.parse(batch, response(batch, "[\""+first+"\"]", "[\""+second+"\"]", "[]")).allCommitted(), "accepted and duplicate items committed");
        check(!BatchAck.parse(batch, response(batch, "[\""+first+"\"]", "[]", "[]")).allCommitted(), "missing item is not success");
        check(!BatchAck.parse(batch, response(batch, "[\""+first+"\"]", "[]", "[{\"sample_id\":\""+second+"\",\"reason\":\"real_data_disabled\"}]")).allCommitted(), "rejection is not success");
        check(!BatchAck.parse(batch, response(batch, "[]", "[]", "[]")).allCommitted(), "empty response is not success");
        BatchAck mixed = BatchAck.parse(batch, response(batch, "[]", "[]",
                "[{\"sample_id\":\""+first+"\",\"reason\":\"real_data_disabled\"},"+
                "{\"sample_id\":\""+second+"\",\"reason\":\"batch_contains_real_data\"}]"));
        check(!mixed.allCommitted() && mixed.committedIds.isEmpty() && mixed.rejected.size()==2 &&
                "real_data_disabled".equals(mixed.rejected.get(first)) &&
                "batch_contains_real_data".equals(mixed.rejected.get(second)),
                "mixed rejection cannot acknowledge either item");
        String generic = "Status upload failed";
        check(UploadOutcome.incomplete(mixed) && UploadOutcome.failureMessage(mixed, generic).contains("Pending data retained"),
                "incomplete acknowledgment survives a later status error");
        BatchAck complete = BatchAck.parse(batch, response(batch, "[\""+first+"\"]", "[\""+second+"\"]", "[]"));
        check(!UploadOutcome.incomplete(complete) && UploadOutcome.followUpMessage().equals(UploadOutcome.failureMessage(complete, generic)) &&
                generic.equals(UploadOutcome.failureMessage(null, generic)), "other status failures remain distinct");
        String incomplete = UploadOutcome.foregroundMessage("Started", mixed, false);
        check(incomplete.equals(UploadOutcome.incompleteMessage()) &&
                "Caregiver operation enabled.".equals(UploadOutcome.foregroundMessage(incomplete, complete, true)),
                "successful retry clears the earlier incomplete message");
        check(UploadOutcome.foregroundMessage(incomplete, complete, false).contains("acknowledged") &&
                "Other status".equals(UploadOutcome.foregroundMessage("Other status", null, false)),
                "acknowledged readings and a failed follow-up remain distinct");
        try {
            BatchAck.parse(batch, response(batch, "[\""+first+"\"]", "[\""+first+"\"]", "[]"));
            throw new AssertionError("duplicate acknowledgment must fail");
        } catch (JSONException expected) { }
        System.out.println("10 synthetic acknowledgment checks passed");
    }
}
