package org.momhealth.gateway.history;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/** Inactive transport state machine: caller supplies events; this class has no BLE/timer/I/O API. */
public final class HistoryTransferPlan {
    public enum State { NEW, COMMAND_PENDING, RECEIVING, TERMINAL_AMBIGUOUS, FAILED, TIMED_OUT, CANCELLED, LIMIT_REACHED }
    public static final int CALLBACKS_PER_WINDOW=50, MAX_WINDOWS=4, MAX_RECORDS=500;
    public static final long MAX_DURATION_MS=120000, IDLE_TIMEOUT_MS=10000;
    public static final class Command {
        public final long token;
        public final HrHistoryCodec.ReadMode mode;
        private final byte[] bytes;
        private Command(long token,HrHistoryCodec.ReadMode mode,byte[] bytes) {
            this.token=token;this.mode=mode;this.bytes=bytes.clone();
        }
        public byte[] bytesForReview() { return bytes.clone(); }
    }
    private final String model,deviceId;
    private final UUID sessionId;
    private final HrHistoryCodec.Kind kind;
    private final List<HrHistoryCodec.Record> records=new ArrayList<>();
    private State state=State.NEW;
    private String reason="Not started; inactive planning only";
    private long started,lastEvent,lastClock,serial;
    private int callbacks,windows;
    private Command pending;
    public HistoryTransferPlan(String model,String deviceId,UUID sessionId,HrHistoryCodec.Kind kind) {
        HistoryCapabilityRegistry.forModel(model);
        if(deviceId==null || deviceId.isEmpty() || deviceId.length()>128 || sessionId==null || kind==null)
            throw new IllegalArgumentException("Invalid transfer identity");
        this.model=model;this.deviceId=deviceId;this.sessionId=sessionId;this.kind=kind;
    }
    public synchronized void begin(long monotonicMs) {
        if(state!=State.NEW || monotonicMs<0) throw new IllegalStateException("Invalid start");
        started=lastEvent=lastClock=monotonicMs;windows=1;
        propose(HrHistoryCodec.ReadMode.START);
    }
    /** Repeated reads return the same token, never enqueue another command. */
    public synchronized Command pendingCommand() { return pending; }
    public synchronized State state() { return state; }
    public synchronized String reason() { return reason; }
    public synchronized List<HrHistoryCodec.Record> snapshot() {
        return Collections.unmodifiableList(new ArrayList<>(records));
    }
    public synchronized void writeResult(String device,UUID session,long token,boolean success,long now) {
        identity(device,session);checkClock(now);
        if(terminal()) return;
        if(pending==null || pending.token!=token) return; // invalidated or duplicate callback
        pending=null;
        if(!success) { finish(State.FAILED,"Read command write failed; no automatic retry");return; }
        state=State.RECEIVING;lastEvent=now;reason="Awaiting history notifications; completion unverified";
    }
    public synchronized void notification(String device,UUID session,byte[] packet,long now) {
        identity(device,session);checkClock(now);
        if(terminal()) return;
        if(state!=State.RECEIVING) { finish(State.FAILED,"Notification before confirmed command; ordering ambiguous");return; }
        if(packet==null || packet.length==0 || packet.length>HrHistoryCodec.MAX_PACKET_BYTES || (packet[0]&255)!=kind.opcode) {
            finish(State.FAILED,"Malformed or unrelated history notification");return;
        }
        // Match the SDK's end heuristic as evidence, not a verified completed transfer.
        if(packet.length<kind.width || (packet[packet.length-1]&255)==255) {
            finish(State.TERMINAL_AMBIGUOUS,"SDK would signal end; framing/truncation or final HR sentinel unresolved. No complete sync claimed");return;
        }
        HrHistoryCodec.Page page;
        try { page=HrHistoryCodec.decodeRecords(model,kind,packet); }
        catch(IllegalArgumentException malformed) { finish(State.FAILED,malformed.getMessage());return; }
        if(records.size()+page.records.size()>MAX_RECORDS) {finish(State.LIMIT_REACHED,"Record limit; partial history only");return;}
        records.addAll(page.records);lastEvent=now;callbacks++;
        if(callbacks==CALLBACKS_PER_WINDOW) {
            callbacks=0;
            if(windows>=MAX_WINDOWS) {finish(State.LIMIT_REACHED,"Read window limit; partial history only");return;}
            windows++;propose(HrHistoryCodec.ReadMode.CONTINUE);
        }
    }
    /** Explicit caller clock, no worker or scheduled/background callback is created. */
    public synchronized void tick(long now) { checkClock(now); }
    public synchronized void cancel() { if(!terminal()) finish(State.CANCELLED,"Cancelled locally; no undocumented stop/delete command"); }
    public synchronized void disconnected(String device,UUID session) {
        identity(device,session);if(!terminal())finish(State.FAILED,"Disconnected; no reconnect/retry planned");
    }
    public synchronized void discard() { cancel(); records.clear(); }
    private void propose(HrHistoryCodec.ReadMode mode) {
        pending=new Command(++serial,mode,HrHistoryCodec.commandForReview(model,kind,mode));
        state=State.COMMAND_PENDING;reason="Review-only command proposed; no device dispatch";
    }
    private void identity(String device,UUID session) {
        if(!deviceId.equals(device) || !sessionId.equals(session))throw new IllegalArgumentException("Foreign device/session event");
    }
    private void checkClock(long now) {
        if(now<0 || (state!=State.NEW && now<lastClock))throw new IllegalArgumentException("Monotonic clock required");
        if(state==State.NEW || terminal())return;
        lastClock=now;
        if(now-started>=MAX_DURATION_MS || now-lastEvent>=IDLE_TIMEOUT_MS)
            finish(State.TIMED_OUT,"Bounded history deadline; partial history only");
    }
    private boolean terminal() { return state!=State.NEW && state!=State.COMMAND_PENDING && state!=State.RECEIVING; }
    private void finish(State next,String why) { state=next;reason=why;pending=null;serial++; }
}
