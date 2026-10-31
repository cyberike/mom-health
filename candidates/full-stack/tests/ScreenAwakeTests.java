import org.momhealth.gateway.*;
public final class ScreenAwakeTests {
    static int count;
    static void check(boolean ok,String reason){count++;if(!ok)throw new AssertionError(reason);}
    static HrSessionTests.Fake prepared(){
        HrSessionTests.Fake f=new HrSessionTests.Fake(new V8Adapter());f.awake.foreground(true);return f;
    }
    public static void main(String[] args){
        HrSessionTests.Fake f=prepared();check(!f.awake.keepAwake(),"foreground idle stays asleep normally");
        f.session.start(34);check(!f.awake.keepAwake(),"rejected insufficient-MTU start never enables flag");
        f.session.start(35);check(f.awake.keepAwake(),"explicit preparation enables flag");
        f.complete(true);check(f.awake.keepAwake(),"start pending retains flag");
        f.complete(true);check(f.awake.keepAwake(),"confirmed acquisition retains flag");
        f.advance(30100);check(!f.awake.keepAwake(),"deadline clears before cleanup completes");
        f.complete(true);f.complete(true);check(!f.awake.keepAwake(),"successful cleanup never keeps awake");
        for(int stage=0;stage<3;stage++){
            HrSessionTests.Fake s=prepared();s.session.start(35);if(stage>=1)s.complete(true);if(stage>=2)s.complete(true);
            s.session.stop("manual stop/disconnect");check(!s.awake.keepAwake(),"Stop clears in every startup/acquisition stage");
            s=prepared();s.session.start(35);if(stage>=1)s.complete(true);if(stage>=2)s.complete(true);
            s.session.transportLost("link error");check(!s.awake.keepAwake(),"transport failure clears in every stage");
        }
        for(int stage=0;stage<2;stage++){
            HrSessionTests.Fake s=prepared();s.session.start(35);if(stage==1)s.complete(true);s.complete(false);
            check(!s.awake.keepAwake(),"enable/start rejection clears immediately");
        }
        HrSessionTests.Fake timeout=prepared();timeout.session.start(35);timeout.advance(5100);
        check(!timeout.awake.keepAwake(),"preparation timeout clears");
        HrSessionTests.Fake life=prepared();life.session.start(35);life.complete(true);life.complete(true);
        life.awake.foreground(false);check(!life.awake.keepAwake(),"pause/lock/exit clears before BLE cleanup");
        life.awake.measurement(true);check(!life.awake.keepAwake(),"late active callback cannot reenable in background");
        life.session.stop("foreground exit");life.awake.foreground(true);check(!life.awake.keepAwake(),"return during cleanup does not reenable");
        life.awake.destroy();life.awake.foreground(true);life.awake.measurement(true);check(!life.awake.keepAwake(),"destroy invalidates all later callbacks");
        HrSessionTests.Fake v1=new HrSessionTests.Fake(new V1Adapter());v1.awake.foreground(true);v1.session.start(23);
        check(v1.awake.keepAwake(),"V1 request also keeps window awake");v1.complete(false);check(!v1.awake.keepAwake(),"V1 failed start clears");
        System.out.println("PASS: "+count+" synthetic screen-awake lifecycle/session assertions; Android window behavior untested.");
    }
}
