package org.momhealth.gateway;

/** Monotonic session gate: unsolicited, old-session and late HR never becomes a current result. */
public final class MeasurementWindow {
    private volatile long generation;
    private long startedAt;
    private boolean active;
    private boolean confirmed;
    private long confirmedAt;
    public long begin(long elapsedMillis) { long token=beginRequest(elapsedMillis); confirm(token); return token; }
    public long beginRequest(long elapsedMillis) { generation++; startedAt=elapsedMillis; active=true; confirmed=false; return generation; }
    public void confirm(long token) { confirm(token,startedAt); }
    public void confirm(long token,long elapsedMillis) { if(active && token==generation) {confirmedAt=elapsedMillis;confirmed=true;} }
    public String phase(long token,long elapsedMillis) {
        if(token!=generation)return "old session";
        if(!active)return "outside session / stopped";
        if(elapsedMillis<startedAt)return "before request";
        if(elapsedMillis-startedAt>=30000)return "at/after 30-second deadline";
        if(!confirmed || elapsedMillis<confirmedAt)return "before start-write confirmation";
        return "inside confirmed request";
    }
    public void stop() { active = false; generation++; }
    public boolean active() { return active; }
    public long generation() { return generation; }
    public boolean accepts(long token, long elapsedMillis, DeviceAdapter.Reading reading) {
        return active && confirmed && token == generation && elapsedMillis >= startedAt && elapsedMillis >= confirmedAt && elapsedMillis - startedAt < 30_000
            && reading.kind == DeviceAdapter.Reading.Kind.HEART_RATE && reading.value != null;
    }
}
