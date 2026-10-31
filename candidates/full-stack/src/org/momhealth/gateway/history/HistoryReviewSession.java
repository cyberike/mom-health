package org.momhealth.gateway.history;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/** Bounded offline inspection session. Not a BLE controller or completed retrieval implementation. */
public final class HistoryReviewSession {
    public static final int MAX_RECORDS=500;
    public static final int MAX_PAGES=50;
    private final String model,deviceId;
    private final UUID sessionId;
    private final HrHistoryCodec.Kind kind;
    private final List<HrHistoryCodec.Record> records=new ArrayList<>();
    private int pages;
    private boolean closed;
    public HistoryReviewSession(String model,String deviceId,UUID sessionId,HrHistoryCodec.Kind kind) {
        HistoryCapabilityRegistry.forModel(model);
        if(deviceId==null || deviceId.length()==0 || deviceId.length()>128 || sessionId==null || kind==null)
            throw new IllegalArgumentException("Missing or invalid session identity");
        this.model=model;this.deviceId=deviceId;this.sessionId=sessionId;this.kind=kind;
    }
    public synchronized void accept(String deviceId,UUID sessionId,byte[] packet) {
        if(closed) throw new IllegalStateException("Review session closed");
        if(!this.deviceId.equals(deviceId) || !this.sessionId.equals(sessionId))
            throw new IllegalArgumentException("Device/session mismatch");
        if(pages>=MAX_PAGES) throw new IllegalStateException("Page limit reached");
        HrHistoryCodec.Page page=HrHistoryCodec.decodeRecords(model,kind,packet);
        if(records.size()+page.records.size()>MAX_RECORDS) throw new IllegalStateException("Record limit reached");
        // Keep repeated records: opaque bytes1–2 have no confirmed sequence/identity semantics.
        // Deduplicating by timestamp or HR would risk silently discarding distinct measurements.
        records.addAll(page.records); pages++;
    }
    public synchronized List<HrHistoryCodec.Record> snapshot() {
        return Collections.unmodifiableList(new ArrayList<>(records));
    }
    public synchronized void close() { closed=true; records.clear(); }
    public synchronized boolean isClosed() { return closed; }
}
