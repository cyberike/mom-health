package org.momhealth.gateway.automation;

import java.util.*;

/** Pure deterministic policy engine. Produces actions; never performs device, storage or network I/O. */
public final class AutomationEngine {
    public enum State { DISABLED, SUSPENDED, WAITING, CONNECTING, READY, STARTING, MEASURING, CLEANING, RETRY_WAIT, ATTENTION }
    public enum Type { CONNECT, START_MEASUREMENT, STOP_MEASUREMENT, DISCONNECT, UPLOAD_BATCH, CANCEL_UPLOAD }
    public static final long ACQUISITION_MS=30000, START_CONFIRM_MS=5000, CLEANUP_MS=16000, CONNECT_TIMEOUT_MS=20000, UPLOAD_TIMEOUT_MS=15000;
    private static final long HOUR=3600000;
    public static final class Action {
        public final Type type; public final long generation,operationId,deadlineMs;
        private Action(Type type,long generation,long operationId,long deadline){this.type=type;this.generation=generation;this.operationId=operationId;this.deadlineMs=deadline;}
    }
    private AutomationConfig config;
    private State state=State.DISABLED;
    private boolean enabled,suspended,radio,network,power,connected,cleanupFault;
    private long generation,serial,lastClock=-1,nextMeasurement,nextConnect,nextUpload,connectOperation,measurementOperation,uploadOperation;
    private long connectDeadline,measurementDeadline,startDeadline,cleanupDeadline,uploadDeadline,confirmedAt,quarantineUntil;
    private int attempts;
    private String status="Disabled; caregiver enablement required";
    private final ArrayDeque<Action> actions=new ArrayDeque<>();
    private final ArrayDeque<Long> sessionStarts=new ArrayDeque<>();
    public synchronized State state(){return state;}
    public synchronized String status(){return status;}
    public synchronized long generation(){return generation;}
    public synchronized long measurementOperationId(){return measurementOperation;}
    public synchronized AutomationConfig config(){return config;}
    public synchronized List<Action> drainActions(){List<Action> result=new ArrayList<>(actions);actions.clear();return Collections.unmodifiableList(result);}
    public synchronized void enable(AutomationConfig policy,long now){
        if(policy==null)throw new IllegalArgumentException("Explicit policy required");clock(now);
        cancelCurrent(now);generation++;config=policy;enabled=true;suspended=false;cleanupFault=false;attempts=0;sessionStarts.clear();
        nextMeasurement=nextConnect=nextUpload=now;state=State.WAITING;status="Enabled; waiting for availability";drive(now);
    }
    public synchronized void environment(boolean radioAvailable,boolean networkAvailable,boolean powerAllowed,long now){
        clock(now);boolean interrupted=(radio&&!radioAvailable)||(power&&!powerAllowed);
        boolean restored=(!radio&&radioAvailable)||(!power&&powerAllowed);
        radio=radioAvailable;network=networkAvailable;power=powerAllowed;
        if(interrupted&&enabled){if(measurementOperation!=0)cleanupFault=true;cancelCurrent(now);generation++;state=cleanupFault?State.ATTENTION:State.WAITING;status=cleanupFault?"Acquisition interrupted; cleanup unknown, caregiver review required":"Paused by radio or power availability";}
        if(!network&&uploadOperation!=0)cancelUpload(now);
        if(restored&&enabled&&!suspended&&!cleanupFault){attempts=0;nextConnect=now;if(state==State.ATTENTION)state=State.WAITING;}
        drive(now);
    }
    public synchronized void tick(long now){clock(now);drive(now);}
    /** Restore a durable cadence/budget delay without suppressing independently consented uploads. */
    public synchronized void deferAcquisitionUntil(long deadlineMs,long now){
        if(!enabled||config==null)return;
        clock(now);
        if(deadlineMs<now||deadlineMs-now>AutomationConfig.MAX_INTERVAL_MS)throw new IllegalArgumentException("Acquisition deferral must be now through 24 hours");
        nextMeasurement=Math.max(nextMeasurement,deadlineMs);
    }
    /** Async callers must retain the generation whose durable budget they are restoring. */
    public synchronized void deferAcquisitionUntil(long expectedGeneration,long deadlineMs,long now){
        if(expectedGeneration!=generation)return;deferAcquisitionUntil(deadlineMs,now);
    }
    public synchronized void suspend(long now){clock(now);if(!enabled)return;cancelCurrent(now);generation++;suspended=true;state=State.SUSPENDED;status="Paused by caregiver; no automatic work";}
    public synchronized void resume(long now){clock(now);if(!enabled)return;suspended=false;cleanupFault=false;attempts=0;nextConnect=now;state=State.WAITING;status="Caregiver resumed";drive(now);}
    public synchronized void revoke(long now){clock(now);cancelCurrent(now);generation++;enabled=false;suspended=false;config=null;sessionStarts.clear();state=State.DISABLED;status="Consent revoked; no automatic work";}
    public synchronized void connectionResult(long gen,long operation,boolean success,long now){
        if(gen!=generation||operation==0||operation!=connectOperation||state!=State.CONNECTING)return;clock(now);
        if(now>=connectDeadline){connectionFailure(now,"Connection deadline exceeded");drive(now);return;}
        connectOperation=0;
        if(!success){connectionFailure(now,"Connection failed");drive(now);return;}
        connected=true;attempts=0;state=State.READY;status="Connected; waiting for scheduled measurement";drive(now);
    }
    public synchronized void connectionLost(long gen,long now){
        if(gen!=generation||!enabled)return;clock(now);if(!connected&&state!=State.CONNECTING)return;
        boolean acquisitionInterrupted=measurementOperation!=0;
        connected=false;connectOperation=0;measurementOperation=0;state=State.RETRY_WAIT;attempts++;
        if(acquisitionInterrupted){cleanupFault=true;state=State.ATTENTION;status="Link lost during acquisition; cleanup unknown, caregiver review required";return;}
        scheduleRetry(now,"Link lost; current reading window invalidated");drive(now);
    }
    public synchronized void measurementStarted(long gen,long operation,long now){
        if(gen!=generation||operation==0||operation!=measurementOperation||state!=State.STARTING)return;clock(now);
        if(now>=startDeadline||now>=measurementDeadline){beginCleanup(now,"Start confirmation late; stopping");return;}
        confirmedAt=now;state=State.MEASURING;status="Bounded measurement active";
    }
    /** Called after controller cleanup completes, not merely when an HR value is returned. */
    public synchronized void measurementFinished(long gen,long operation,boolean cleanupConfirmed,long now){
        if(gen!=generation||operation==0||operation!=measurementOperation)return;clock(now);
        if(!cleanupConfirmed){beginCleanup(now,"Controller cleanup unconfirmed; bounded shutdown pending");return;}
        measurementOperation=0;state=connected?State.READY:State.WAITING;status="Measurement ended; awaiting next interval";drive(now);
    }
    public synchronized boolean uploadFinished(long gen,long operation,boolean success,long now){
        if(gen!=generation||operation==0||operation!=uploadOperation)return false;clock(now);uploadOperation=0;
        status=success?"Upload attempt completed; next attempt follows configured interval":"Upload failed; pending data must remain buffered";
        return true;
    }
    /** Only confirmed current acquisition observations are eligible; recording consent is independent. */
    public synchronized boolean acceptsObservation(long gen,long operation,long arrivalMs){
        return enabled&&!suspended&&power&&radio&&connected&&config!=null&&config.recordingConsent&&config.acquisitionConsent
            &&gen==generation&&operation!=0&&operation==measurementOperation&&state==State.MEASURING
            &&arrivalMs>=confirmedAt&&arrivalMs<measurementDeadline;
    }
    private void drive(long now){
        if(!enabled||suspended||config==null||!power||now<quarantineUntil)return;
        if(uploadOperation!=0&&now>=uploadDeadline){cancelUpload(now);status="Upload deadline; pending data must remain buffered";}
        if(config.uploadConsent&&network&&uploadOperation==0&&now>=nextUpload){
            uploadOperation=++serial;uploadDeadline=after(now,UPLOAD_TIMEOUT_MS);nextUpload=after(now,config.uploadIntervalMs);emit(Type.UPLOAD_BATCH,uploadOperation,uploadDeadline);
        }
        if(!radio||!config.acquisitionConsent||cleanupFault)return;
        while(!sessionStarts.isEmpty()&&now-sessionStarts.peekFirst()>=HOUR)sessionStarts.removeFirst();
        if(state==State.CONNECTING&&now>=connectDeadline)connectionFailure(now,"Connection timed out");
        if(state==State.STARTING&&(now>=startDeadline||now>=measurementDeadline))beginCleanup(now,"Start confirmation deadline; stopping");
        if(state==State.MEASURING&&now>=measurementDeadline)beginCleanup(now,"30-second measurement deadline");
        if(state==State.CLEANING&&now>=cleanupDeadline){
            emit(Type.DISCONNECT,++serial,now);connected=false;measurementOperation=0;cleanupFault=true;state=State.ATTENTION;status="Cleanup unconfirmed; caregiver review required before restart";return;
        }
        if(state==State.ATTENTION||state==State.CONNECTING||state==State.STARTING||state==State.MEASURING||state==State.CLEANING)return;
        if(now<nextMeasurement)return;
        if(sessionStarts.size()>=config.maxSessionsPerHour){status="Hourly acquisition budget reached; waiting for budget renewal";return;}
        if(!connected){
            if(now<nextConnect)return;
            if(attempts>=config.maxConnectAttempts){state=State.ATTENTION;status="Connection attempt budget exhausted; caregiver resume or availability change required";return;}
            attempts++;connectOperation=++serial;connectDeadline=after(now,CONNECT_TIMEOUT_MS);state=State.CONNECTING;status="Connecting to caregiver-selected watch";emit(Type.CONNECT,connectOperation,connectDeadline);return;
        }
        measurementOperation=++serial;measurementDeadline=after(now,ACQUISITION_MS);startDeadline=after(now,START_CONFIRM_MS);
        nextMeasurement=after(now,config.intervalMs);sessionStarts.addLast(now);state=State.STARTING;status="Requesting one bounded measurement";
        emit(Type.START_MEASUREMENT,measurementOperation,measurementDeadline);
    }
    private void beginCleanup(long now,String why){
        if(measurementOperation==0)return;
        if(state!=State.CLEANING){state=State.CLEANING;cleanupDeadline=after(now,CLEANUP_MS);emit(Type.STOP_MEASUREMENT,measurementOperation,cleanupDeadline);}
        status=why;
    }
    private void connectionFailure(long now,String reason){emit(Type.DISCONNECT,++serial,now);connected=false;connectOperation=0;scheduleRetry(now,reason);}
    private void scheduleRetry(long now,String reason){
        if(attempts>=config.maxConnectAttempts){state=State.ATTENTION;status=reason+"; retry budget exhausted";return;}
        state=State.RETRY_WAIT;nextConnect=after(now,Math.min(60000,2000L<<(Math.max(0,attempts-1))));status=reason+"; bounded reconnect backoff";
    }
    private void cancelCurrent(long now){
        // Drop unconsumed start/connect work before enqueuing bounded teardown for potentially dispatched work.
        actions.clear();if(connected||connectOperation!=0||measurementOperation!=0)quarantineUntil=after(now,CLEANUP_MS);
        if(measurementOperation!=0)emit(Type.STOP_MEASUREMENT,measurementOperation,after(now,CLEANUP_MS));
        if(connected||connectOperation!=0||measurementOperation!=0)emit(Type.DISCONNECT,++serial,after(now,CLEANUP_MS));
        cancelUpload(now);connected=false;connectOperation=measurementOperation=0;
    }
    private void cancelUpload(long now){if(uploadOperation!=0){emit(Type.CANCEL_UPLOAD,uploadOperation,now);uploadOperation=0;}}
    private void emit(Type type,long operation,long deadline){if(actions.size()>=32)throw new IllegalStateException("Caller must drain actions after every event");actions.addLast(new Action(type,generation,operation,deadline));}
    private void clock(long now){if(now<0||now<lastClock)throw new IllegalArgumentException("Monotonic nonnegative time required");lastClock=now;}
    private static long after(long now,long delta){return now>Long.MAX_VALUE-delta?Long.MAX_VALUE:now+delta;}
}
