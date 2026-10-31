package org.momhealth.gateway;

/** Original, Android-independent bounded acquisition/cleanup state machine. */
public final class HrSession {
    public interface Completion { void complete(boolean success); }
    public interface Driver {
        long now();
        void write(byte[] bytes,String label,Completion completion);
        void later(long delay,Runnable action);
        void event(String text);
        void changed();
        void finished(boolean cleanupConfirmed);
    }
    public enum Stage { IDLE, ENABLING, STARTING, ACTIVE, CLEANUP }
    private final DeviceAdapter adapter;
    private final Driver driver;
    private final MeasurementWindow window=new MeasurementWindow();
    private Stage stage=Stage.IDLE;
    private long run;
    private boolean streamConfirmed,stopConfirmed,disableConfirmed;
    public HrSession(DeviceAdapter adapter,Driver driver) {this.adapter=adapter;this.driver=driver;}
    public boolean busy() {return stage!=Stage.IDLE;}
    public boolean collecting() {return stage==Stage.ENABLING || stage==Stage.STARTING || stage==Stage.ACTIVE;}
    public Stage stage() {return stage;}
    public long token() {return window.generation();}
    public String phase(long token,long at) {return window.phase(token,at);}
    public boolean accepts(long token,long at,DeviceAdapter.Reading reading) {
        return stage==Stage.ACTIVE && (!adapter.needsReporting() || streamConfirmed) && window.accepts(token,at,reading);
    }
    public void start(int mtu) {
        if(busy())return;
        if(mtu<adapter.minimumMtu()) {driver.event("Insufficient MTU; no reporting or HR command sent.");return;}
        final long id=++run;
        streamConfirmed=false;stopConfirmed=false;disableConfirmed=!adapter.needsReporting();window.stop();
        if(adapter.needsReporting()) {
            stage=Stage.ENABLING;driver.changed();
            driver.later(5000,() -> {if(run==id && stage==Stage.ENABLING)stop("Reporting setup deadline");});
            driver.write(adapter.enableReporting(),"Enable V8 reporting (temperature off)",ok -> {
                if(run!=id || stage!=Stage.ENABLING)return;
                if(!ok) {stop("Reporting enable failed or unconfirmed");return;}
                streamConfirmed=true;startHr(id);
            });
        } else startHr(id);
    }
    private void startHr(long id) {
        stage=Stage.STARTING;
        final long token=window.beginRequest(driver.now());driver.changed();
        driver.later(30000,() -> {if(run==id && collecting())stop("30-second HR deadline");});
        driver.write(adapter.startHeartRate(),"Start HR (30 seconds)",ok -> {
            if(run!=id || stage!=Stage.STARTING)return;
            if(!ok) {stop("HR start failed or unconfirmed");return;}
            window.confirm(token,driver.now());stage=Stage.ACTIVE;driver.changed();
            driver.event("HR write confirmed; original 30-second deadline unchanged.");
        });
    }
    public void stop(String reason) {
        if(stage==Stage.IDLE || stage==Stage.CLEANUP)return;
        final long id=run;
        window.stop();stage=Stage.CLEANUP;driver.changed();
        driver.event(reason+". HR invalidated; stopping HR"+(adapter.needsReporting()?" then disabling reporting.":"."));
        // Includes any already submitted write plus two ordered cleanup writes (5 s each).
        driver.later(16000,() -> {if(run==id && stage==Stage.CLEANUP)finish(false,"Cleanup deadline; delivery unconfirmed. Check device.");});
        driver.write(adapter.stopHeartRate(),"Stop HR",ok -> {
            if(run!=id || stage!=Stage.CLEANUP)return;
            stopConfirmed=ok;
            if(adapter.needsReporting()) {
                driver.write(adapter.disableReporting(),"Disable V8 reporting",disabled -> {
                    if(run!=id || stage!=Stage.CLEANUP)return;
                    disableConfirmed=disabled;finish(stopConfirmed && disableConfirmed,"Cleanup writes completed");
                });
            } else finish(stopConfirmed,"HR stop write completed");
        });
    }
    public void transportLost(String reason) {
        if(!busy())return;
        finish(false,reason+"; remaining HR stop/reporting disable cannot be safely delivered");
    }
    private void finish(boolean confirmed,String reason) {
        window.stop();run++;stage=Stage.IDLE;streamConfirmed=false;
        driver.event(reason+". HR stop="+stopConfirmed+", reporting disable="+disableConfirmed
            +" (GATT confirmation only; physical shutdown unverified).");
        driver.changed();driver.finished(confirmed);
    }
}
