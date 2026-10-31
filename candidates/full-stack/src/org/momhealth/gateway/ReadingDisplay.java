package org.momhealth.gateway;

/** Presentation only. Freshness means recent receipt, never physiological accuracy. */
public final class ReadingDisplay {
    public static final long STALE_AFTER_MS=5000;
    private Integer bpm;
    private long receivedWall,receivedElapsed;
    private boolean active,currentSession,ended;
    public void session(boolean collecting) {
        if(collecting && !active) {currentSession=false;ended=false;}
        if(!collecting && active)ended=true;
        active=collecting;
    }
    public void accept(int value,long wall,long elapsed) {
        if(!active || value<20 || value>250)return;
        bpm=value;receivedWall=wall;receivedElapsed=elapsed;currentSession=true;
    }
    public void reset(){bpm=null;active=false;currentSession=false;ended=false;receivedWall=0;receivedElapsed=0;}
    public Integer bpm(){return bpm;}
    public long receivedWall(){return receivedWall;}
    public boolean current(long now){return active && currentSession && bpm!=null && now>=receivedElapsed && now-receivedElapsed<STALE_AFTER_MS;}
    public String label(long now){
        if(bpm==null)return active?"Waiting for a reading":ended?"Measurement ended - no reading received":"No reading yet";
        if(!active)return ended?"Measurement ended - last reading only":"Not measuring - last reading only";
        if(!currentSession)return bpm==null?"Waiting for a reading":"Waiting - previous measurement shown";
        return current(now)?"Current session - recently received":"Stale - no new reading for 5 seconds";
    }
}
