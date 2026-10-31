package org.momhealth.gateway.data;

/** Preserve a known incomplete batch result if a later status operation fails. */
public final class UploadOutcome {
    private UploadOutcome() { }

    public static boolean incomplete(BatchAck ack) {
        return ack != null && !ack.allCommitted();
    }

    public static String incompleteMessage() {
        return "Some readings were rejected or not acknowledged. Pending data retained; caregiver review required.";
    }

    public static String followUpMessage() {
        return "Readings were acknowledged, but the upload follow-up did not complete. Review connection and status.";
    }

    public static String failureMessage(BatchAck ack, String otherwise) {
        return incomplete(ack) ? incompleteMessage() : ack != null ? followUpMessage() : otherwise;
    }

    public static String foregroundMessage(String previous, BatchAck ack, boolean success) {
        if (incomplete(ack)) return incompleteMessage();
        if (ack != null && !success) return followUpMessage();
        if (success && (incompleteMessage().equals(previous) ||
                followUpMessage().equals(previous)))
            return "Caregiver operation enabled.";
        return previous;
    }
}
