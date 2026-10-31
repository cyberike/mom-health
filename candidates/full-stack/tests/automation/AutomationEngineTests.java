package org.momhealth.gateway.automation;
import java.util.*;

/** Synthetic event/clock tests only. No Android, BLE, database or network operations. */
public final class AutomationEngineTests {
    private static int checks;
    private static final String ID="00000000-0000-0000-0000-000000000001";
    private static void check(boolean value){checks++;if(!value)throw new AssertionError("Check "+checks);}
    private static void invalid(Runnable r){checks++;try{r.run();}catch(IllegalArgumentException expected){return;}throw new AssertionError("Expected validation failure "+checks);}
    private static AutomationConfig config(boolean acquire,boolean record,boolean upload,int sessions,int connects){return new AutomationConfig(ID,"V8",60000,acquire,record,upload,sessions,connects,60000);}
    private static AutomationEngine engine(AutomationConfig config){AutomationEngine e=new AutomationEngine();e.environment(true,true,true,0);e.enable(config,0);return e;}
    private static AutomationEngine.Action one(AutomationEngine e,AutomationEngine.Type type){List<AutomationEngine.Action> a=e.drainActions();check(a.size()==1);check(a.get(0).type==type);return a.get(0);}
    private static AutomationEngine.Action start(AutomationEngine e){AutomationEngine.Action c=one(e,AutomationEngine.Type.CONNECT);e.connectionResult(c.generation,c.operationId,true,100);return one(e,AutomationEngine.Type.START_MEASUREMENT);}
    private static boolean contains(List<AutomationEngine.Action> actions,AutomationEngine.Type type){for(AutomationEngine.Action a:actions)if(a.type==type)return true;return false;}
    public static void main(String[] args){
        configuration(); lifecycle(); failures(); interruptions(); budgets(); upload(); identities(); consentMatrix(); durableDeferral();
        System.out.println("PASS: "+checks+" automation policy assertions; synthetic clocks/events, no device/network execution.");
    }
    private static void configuration(){
        AutomationConfig c=config(true,true,false,2,3);check(AutomationConfig.fromMap(c.toMap()).deviceId.equals(ID));
        check(AutomationConfig.fromMap(c.toMap()).intervalMs==60000);check(!AutomationConfig.fromMap(c.toMap()).uploadConsent);
        invalid(()->new AutomationConfig(ID,"V4",60000,true,true,false,1,1,60000));
        invalid(()->new AutomationConfig("AA:BB:CC:DD:EE:FF","V8",60000,true,true,false,1,1,60000));
        for(long bad:new long[]{-1,0,45999,86400001})invalid(()->new AutomationConfig(ID,"V8",bad,true,true,false,1,1,60000));
        for(int bad:new int[]{-1,0,61})invalid(()->config(true,true,false,bad,1));
        for(int bad:new int[]{-1,0,7})invalid(()->config(true,true,false,1,bad));
        invalid(()->new AutomationConfig(ID,"V8",60000,true,true,true,1,1,59999));
        Map<String,String> m=new HashMap<>(c.toMap());m.put("acquisitionConsent","yes");invalid(()->AutomationConfig.fromMap(m));
        m.put("acquisitionConsent","true");m.put("extra","unexpected");invalid(()->AutomationConfig.fromMap(m));m.remove("extra");m.remove("deviceId");invalid(()->AutomationConfig.fromMap(m));
        AutomationEngine off=new AutomationEngine();off.environment(true,true,true,0);off.tick(600000);check(off.drainActions().isEmpty());check(off.state()==AutomationEngine.State.DISABLED);
        AutomationEngine noConsent=engine(config(false,true,false,2,2));check(noConsent.drainActions().isEmpty());
    }
    private static void lifecycle(){
        AutomationEngine e=engine(config(true,true,false,2,3));AutomationEngine.Action s=start(e);
        check(s.deadlineMs==30100);check(!e.acceptsObservation(s.generation,s.operationId,150));
        e.measurementStarted(s.generation,s.operationId,200);check(e.acceptsObservation(s.generation,s.operationId,200));check(!e.acceptsObservation(s.generation,s.operationId,199));
        e.tick(30099);check(e.drainActions().isEmpty());check(e.acceptsObservation(s.generation,s.operationId,30099));
        e.tick(30100);AutomationEngine.Action stop=one(e,AutomationEngine.Type.STOP_MEASUREMENT);check(stop.operationId==s.operationId);check(!e.acceptsObservation(s.generation,s.operationId,30100));
        e.tick(30101);check(e.drainActions().isEmpty());e.measurementFinished(s.generation,s.operationId,true,30200);check(e.state()==AutomationEngine.State.READY);
        e.tick(60099);check(e.drainActions().isEmpty());e.tick(60100);AutomationEngine.Action next=one(e,AutomationEngine.Type.START_MEASUREMENT);check(next.operationId!=s.operationId);
        e.measurementStarted(s.generation,s.operationId,60200);check(e.state()==AutomationEngine.State.STARTING);
        e.measurementStarted(next.generation,next.operationId,60201);check(e.state()==AutomationEngine.State.MEASURING);
        e.revoke(60300);List<AutomationEngine.Action> a=e.drainActions();check(contains(a,AutomationEngine.Type.STOP_MEASUREMENT));check(contains(a,AutomationEngine.Type.DISCONNECT));check(e.config()==null);
        check(!e.acceptsObservation(next.generation,next.operationId,60250));e.tick(999999);check(e.drainActions().isEmpty());
    }
    private static void failures(){
        AutomationEngine e=engine(config(true,true,false,2,2));AutomationEngine.Action c=one(e,AutomationEngine.Type.CONNECT);
        e.tick(20000);one(e,AutomationEngine.Type.DISCONNECT);check(e.state()==AutomationEngine.State.RETRY_WAIT);
        e.connectionResult(c.generation,c.operationId,true,20001);check(e.state()==AutomationEngine.State.RETRY_WAIT);
        e.tick(21999);check(e.drainActions().isEmpty());e.tick(22000);one(e,AutomationEngine.Type.CONNECT);
        e.tick(42000);one(e,AutomationEngine.Type.DISCONNECT);check(e.state()==AutomationEngine.State.ATTENTION);e.tick(999999);check(e.drainActions().isEmpty());
        AutomationEngine late=engine(config(true,true,false,2,2));AutomationEngine.Action s=start(late);
        late.measurementStarted(s.generation,s.operationId,5100);one(late,AutomationEngine.Type.STOP_MEASUREMENT);check(late.state()==AutomationEngine.State.CLEANING);
        late.measurementFinished(s.generation,s.operationId,false,6000);check(late.drainActions().isEmpty());
        late.tick(21100);one(late,AutomationEngine.Type.DISCONNECT);check(late.state()==AutomationEngine.State.ATTENTION);
        late.measurementFinished(s.generation,s.operationId,true,22000);check(late.state()==AutomationEngine.State.ATTENTION);
    }
    private static void interruptions(){
        AutomationEngine e=engine(config(true,true,false,2,3));AutomationEngine.Action s=start(e);e.measurementStarted(s.generation,s.operationId,200);
        e.suspend(300);List<AutomationEngine.Action> a=e.drainActions();check(contains(a,AutomationEngine.Type.STOP_MEASUREMENT));check(contains(a,AutomationEngine.Type.DISCONNECT));check(e.state()==AutomationEngine.State.SUSPENDED);
        e.tick(1000);check(e.drainActions().isEmpty());e.resume(1001);check(e.drainActions().isEmpty()); // teardown quarantine
        e.tick(16300);check(e.drainActions().isEmpty()); // measurement cadence still respected
        e.tick(60100);one(e,AutomationEngine.Type.CONNECT);
        long gen=e.generation();e.environment(false,true,true,60200);check(contains(e.drainActions(),AutomationEngine.Type.DISCONNECT));check(e.generation()!=gen);
        e.environment(true,true,true,60201);check(e.drainActions().isEmpty());e.tick(76200);one(e,AutomationEngine.Type.CONNECT);
        e.environment(true,true,false,76300);one(e,AutomationEngine.Type.DISCONNECT);e.tick(200000);check(e.drainActions().isEmpty());
        e.environment(true,true,true,200001);one(e,AutomationEngine.Type.CONNECT);
    }
    private static void budgets(){
        AutomationEngine e=engine(config(true,true,false,1,3));AutomationEngine.Action s=start(e);e.measurementStarted(s.generation,s.operationId,200);e.measurementFinished(s.generation,s.operationId,true,1000);
        e.tick(60100);check(e.drainActions().isEmpty());check(e.status().contains("budget"));e.tick(3600099);check(e.drainActions().isEmpty());
        e.tick(3600100);one(e,AutomationEngine.Type.START_MEASUREMENT);
        AutomationEngine n=engine(config(true,false,false,2,3));AutomationEngine.Action sn=start(n);n.measurementStarted(sn.generation,sn.operationId,200);check(!n.acceptsObservation(sn.generation,sn.operationId,201));
    }
    private static void upload(){
        AutomationEngine e=engine(config(false,false,true,1,1));AutomationEngine.Action u=one(e,AutomationEngine.Type.UPLOAD_BATCH);
        e.tick(14999);check(e.drainActions().isEmpty());e.tick(15000);one(e,AutomationEngine.Type.CANCEL_UPLOAD);e.tick(59999);check(e.drainActions().isEmpty());
        e.tick(60000);AutomationEngine.Action n=one(e,AutomationEngine.Type.UPLOAD_BATCH);check(!e.uploadFinished(u.generation,u.operationId,true,60001));check(e.drainActions().isEmpty());
        e.environment(true,false,true,60002);AutomationEngine.Action cancel=one(e,AutomationEngine.Type.CANCEL_UPLOAD);check(cancel.operationId==n.operationId);
        e.environment(true,true,true,60003);check(e.drainActions().isEmpty());e.tick(120000);one(e,AutomationEngine.Type.UPLOAD_BATCH);
        e.revoke(120001);one(e,AutomationEngine.Type.CANCEL_UPLOAD);e.tick(240000);check(e.drainActions().isEmpty());
        AutomationEngine current=engine(config(false,false,true,1,1));AutomationEngine.Action op=one(current,AutomationEngine.Type.UPLOAD_BATCH);
        check(current.uploadFinished(op.generation,op.operationId,false,1));check(!current.uploadFinished(op.generation,op.operationId,false,2));
    }
    private static void consentMatrix(){
        for(boolean acquire:new boolean[]{false,true})for(boolean record:new boolean[]{false,true})for(boolean upload:new boolean[]{false,true}){
            AutomationEngine e=engine(config(acquire,record,upload,1,2));List<AutomationEngine.Action> initial=e.drainActions();
            check(contains(initial,AutomationEngine.Type.CONNECT)==acquire);check(contains(initial,AutomationEngine.Type.UPLOAD_BATCH)==upload);
            if(acquire){AutomationEngine.Action connect=null;for(AutomationEngine.Action a:initial)if(a.type==AutomationEngine.Type.CONNECT)connect=a;
                e.connectionResult(connect.generation,connect.operationId,true,100);AutomationEngine.Action start=one(e,AutomationEngine.Type.START_MEASUREMENT);
                e.measurementStarted(start.generation,start.operationId,200);check(e.acceptsObservation(start.generation,start.operationId,201)==record);
            }
            e.revoke(1000);e.drainActions();e.tick(1000000);check(e.drainActions().isEmpty());
        }
        AutomationEngine idle=engine(config(true,true,false,2,3));AutomationEngine.Action s=start(idle);idle.measurementStarted(s.generation,s.operationId,200);
        idle.measurementFinished(s.generation,s.operationId,true,500);idle.connectionLost(s.generation,1000);check(idle.state()==AutomationEngine.State.RETRY_WAIT);
        idle.tick(60100);one(idle,AutomationEngine.Type.CONNECT);
        AutomationEngine interrupted=engine(config(true,true,false,2,3));AutomationEngine.Action active=start(interrupted);interrupted.measurementStarted(active.generation,active.operationId,200);
        interrupted.environment(false,true,true,201);check(interrupted.state()==AutomationEngine.State.ATTENTION);check(contains(interrupted.drainActions(),AutomationEngine.Type.STOP_MEASUREMENT));
        interrupted.environment(true,true,true,202);interrupted.tick(100000);check(interrupted.drainActions().isEmpty());check(interrupted.state()==AutomationEngine.State.ATTENTION);
    }
    private static void durableDeferral(){
        AutomationEngine e=new AutomationEngine();e.enable(config(true,true,true,2,3),0);
        long gen=e.generation();e.deferAcquisitionUntil(gen,120000,0);e.environment(true,true,true,1);
        one(e,AutomationEngine.Type.UPLOAD_BATCH);e.deferAcquisitionUntil(60000,2); // cannot pull 120s deadline earlier
        e.tick(60001);List<AutomationEngine.Action> due=e.drainActions();check(contains(due,AutomationEngine.Type.CANCEL_UPLOAD));check(contains(due,AutomationEngine.Type.UPLOAD_BATCH));check(!contains(due,AutomationEngine.Type.CONNECT));
        e.tick(119999);e.drainActions();e.tick(120000);one(e,AutomationEngine.Type.CONNECT);
        e.revoke(120001);e.drainActions();e.deferAcquisitionUntil(0,-1);check(e.state()==AutomationEngine.State.DISABLED);
        e.enable(config(true,true,false,2,3),140000);AutomationEngine.Action c=one(e,AutomationEngine.Type.CONNECT);
        e.deferAcquisitionUntil(gen,200000,140001); // stale restore cannot alter new generation
        e.connectionResult(c.generation,c.operationId,true,140002);AutomationEngine.Action s=one(e,AutomationEngine.Type.START_MEASUREMENT);
        e.measurementStarted(s.generation,s.operationId,140003);e.deferAcquisitionUntil(250000,140004);
        e.tick(s.deadlineMs);one(e,AutomationEngine.Type.STOP_MEASUREMENT);check(!e.acceptsObservation(s.generation,s.operationId,s.deadlineMs));
        e.measurementFinished(s.generation,s.operationId,true,170003);e.tick(249999);check(e.drainActions().isEmpty());e.tick(250000);one(e,AutomationEngine.Type.START_MEASUREMENT);
        invalid(()->e.deferAcquisitionUntil(249999,250001));invalid(()->e.deferAcquisitionUntil(250001+AutomationConfig.MAX_INTERVAL_MS+1,250001));
    }
    private static void identities(){
        AutomationEngine e=engine(config(true,true,false,2,3));AutomationEngine.Action s=start(e);e.measurementStarted(s.generation,s.operationId,200);
        check(!e.acceptsObservation(s.generation+1,s.operationId,201));check(!e.acceptsObservation(s.generation,s.operationId+1,201));
        e.connectionLost(s.generation+1,202);check(e.state()==AutomationEngine.State.MEASURING);
        e.connectionLost(s.generation,202);check(!e.acceptsObservation(s.generation,s.operationId,203));
        check(e.state()==AutomationEngine.State.ATTENTION);e.tick(60100);check(e.drainActions().isEmpty());
        e.environment(false,false,true,60101);e.drainActions();e.environment(true,true,true,60102);check(e.drainActions().isEmpty());check(e.state()==AutomationEngine.State.ATTENTION);
        e.resume(60103);one(e,AutomationEngine.Type.CONNECT);
        invalid(()->e.tick(0));invalid(()->e.tick(-1));
        AutomationEngine cold=new AutomationEngine();cold.enable(config(true,true,false,1,1),0);check(cold.drainActions().isEmpty());cold.environment(false,false,true,1);check(cold.drainActions().isEmpty());cold.environment(true,false,true,2);one(cold,AutomationEngine.Type.CONNECT);
    }
}
