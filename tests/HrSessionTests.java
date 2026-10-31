import org.momhealth.gateway.*;
import java.util.*;

/** Deterministic synthetic protocol/session tests, not Android or watch validation. */
public final class HrSessionTests {
    static int count;
    static void check(boolean ok,String why) {count++;if(!ok)throw new AssertionError(why);}
    static final class Write {
        byte[] bytes; HrSession.Completion done;
        Write(byte[] b,HrSession.Completion c){bytes=b;done=c;}
    }
    static final class Timer {
        long at;Runnable action;
        Timer(long a,Runnable r){at=a;action=r;}
    }
    static final class Fake implements HrSession.Driver {
        long time=100; List<Write> writes=new ArrayList<>();ArrayDeque<Write> queue=new ArrayDeque<>();
        List<Timer> timers=new ArrayList<>();List<String> events=new ArrayList<>();
        final ScreenAwakeState awake=new ScreenAwakeState();
        Boolean finished; HrSession session; DeviceAdapter adapter;
        Fake(DeviceAdapter a){adapter=a;session=new HrSession(a,this);}
        public long now(){return time;}
        public void write(byte[] b,String label,HrSession.Completion c){check(adapter.permits(b),"all session writes allowlisted");Write w=new Write(b,c);writes.add(w);queue.add(w);}
        public void later(long delay,Runnable r){timers.add(new Timer(time+delay,r));}
        public void event(String e){events.add(e);}
        public void changed(){awake.measurement(session.collecting());}
        public void finished(boolean c){finished=c;}
        void complete(boolean ok){check(!queue.isEmpty(),"completion has queued write");queue.remove().done.complete(ok);}
        void advance(long target){while(true){Timer next=null;for(Timer t:timers)if(t.at<=target && (next==null||t.at<next.at))next=t;if(next==null)break;timers.remove(next);time=next.at;next.action.run();}time=target;}
        void command(int n,byte[] expected){check(Arrays.equals(writes.get(n).bytes,expected),"ordered command "+n);}
    }
    static DeviceAdapter.Reading candidate(){byte[] p=new byte[32];p[0]=9;p[21]=72;return new V8Adapter().decode(p);}
    static Fake active(){Fake f=new Fake(new V8Adapter());f.session.start(35);f.complete(true);f.complete(true);return f;}
    public static void main(String[] args){
        V8Adapter v=new V8Adapter();
        Fake low=new Fake(v);low.session.start(34);check(low.writes.isEmpty()&&!low.session.busy(),"insufficient MTU sends nothing");
        Fake f=new Fake(v);f.session.start(35);long old=f.session.token();
        check(f.session.stage()==HrSession.Stage.ENABLING && f.writes.size()==1,"reporting prerequisite before HR");
        check(!f.session.accepts(old,f.time,candidate()),"no HR while enabling");f.command(0,v.enableReporting());
        f.time=200;f.complete(true);long token=f.session.token();f.command(1,v.startHeartRate());
        check(!f.session.accepts(token,200,candidate()),"no HR before start confirmation");
        f.time=300;f.complete(true);check(f.session.accepts(token,300,candidate()),"confirmed current HR accepted");
        check(!f.session.accepts(token,299,candidate()),"pre-confirmation arrival rejected");
        check(!f.session.accepts(old,301,candidate()),"old session token rejected");
        byte[] zero=new byte[25];zero[0]=9;check(!f.session.accepts(token,301,v.decode(zero)),"zero is no measurement");
        check(f.session.accepts(token,30199,candidate()),"last millisecond inside original deadline");
        check(!f.session.accepts(token,30200,candidate()),"deadline exclusive");
        f.advance(30200);check(f.session.stage()==HrSession.Stage.CLEANUP,"timeout initiates cleanup");f.command(2,v.stopHeartRate());
        check(!f.session.accepts(token,30200,candidate()),"no late result during cleanup");
        f.complete(true);f.command(3,v.disableReporting());f.complete(true);
        check(Boolean.TRUE.equals(f.finished)&&!f.session.busy(),"both cleanup confirmed");
        f.session.start(35);check(!f.session.accepts(token,f.time,candidate()),"new run cannot reuse old token");

        for(int stage=0;stage<3;stage++)for(int stopOk=0;stopOk<2;stopOk++)for(int disableOk=0;disableOk<2;disableOk++){
            Fake s=new Fake(v);s.session.start(35);if(stage>=1)s.complete(true);if(stage>=2)s.complete(true);
            int before=s.writes.size();s.session.stop("Stop/exit");s.session.stop("duplicate Stop");
            check(s.writes.size()==before+1,"duplicate stop idempotent");s.command(before,v.stopHeartRate());
            if(stage<2)s.complete(true); // outstanding startup completes after Stop, must not advance startup
            check(s.writes.size()==before+1,"late startup callback does not start/restart HR");
            s.complete(stopOk==1);s.command(before+1,v.disableReporting());s.complete(disableOk==1);
            check(s.finished==(stopOk==1&&disableOk==1),"cleanup failures remain visible");
        }
        for(int failureStage=0;failureStage<2;failureStage++){
            Fake s=new Fake(v);s.session.start(35);if(failureStage==1)s.complete(true);s.complete(false);
            check(s.session.stage()==HrSession.Stage.CLEANUP,"rejected setup/start enters cleanup");
            s.command(s.writes.size()-1,v.stopHeartRate());s.complete(false);s.command(s.writes.size()-1,v.disableReporting());s.complete(true);
            check(Boolean.FALSE.equals(s.finished),"failed stop still attempts disable but not confirmed");
        }
        Fake delayed=new Fake(v);delayed.session.start(35);delayed.advance(5100);
        check(delayed.session.stage()==HrSession.Stage.CLEANUP,"bounded reporting startup");
        delayed.complete(true);check(delayed.writes.size()==2,"late enable cannot start HR");
        delayed.complete(true);delayed.complete(true);check(Boolean.TRUE.equals(delayed.finished),"startup timeout still cleans up");
        Fake missing=active();missing.session.stop("Stop");HrSession.Completion late=missing.queue.peek().done;
        missing.advance(16100);check(Boolean.FALSE.equals(missing.finished)&&!missing.session.busy(),"bounded missing cleanup callback");
        int n=missing.writes.size();late.complete(true);check(missing.writes.size()==n,"stale cleanup callback cannot submit disable after close");
        for(int stage=0;stage<4;stage++){
            Fake lost=new Fake(v);lost.session.start(35);if(stage>=1)lost.complete(true);if(stage>=2)lost.complete(true);if(stage==3)lost.session.stop("Stop");
            List<Write> snapshot=new ArrayList<>(lost.queue);int before=lost.writes.size();
            lost.session.transportLost("disconnect or ambiguous queue timeout");
            for(Write w:snapshot)w.done.complete(true);
            check(Boolean.FALSE.equals(lost.finished)&&!lost.session.busy(),"disconnect unconfirmed and idle");
            check(lost.writes.size()==before,"quarantined callbacks cannot issue commands");
        }
        Fake oldTimer=active();oldTimer.session.stop("Stop");oldTimer.complete(true);oldTimer.complete(true);
        oldTimer.time=1000;oldTimer.session.start(35);oldTimer.complete(true);oldTimer.complete(true);oldTimer.advance(30100);
        check(oldTimer.session.stage()==HrSession.Stage.ACTIVE,"previous run deadline cannot stop next run");
        Fake v1=new Fake(new V1Adapter());v1.session.start(23);check(v1.writes.size()==1,"V1 starts without reporting or MTU change");
        v1.complete(true);v1.advance(30100);v1.complete(true);check(v1.writes.size()==2&&Boolean.TRUE.equals(v1.finished),"V1 start then stop only");
        System.out.println("PASS: "+count+" synthetic HR session assertions; no device or Android GATT execution.");
    }
}
