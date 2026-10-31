import org.momhealth.gateway.*;
public final class WatchSelectionTests {
    static int count;
    static void check(boolean ok,String why){count++;if(!ok)throw new AssertionError(why);}
    public static void main(String[] args){
        WatchSelection<String> w=new WatchSelection<>();ReadingDisplay d=new ReadingDisplay();
        check(!w.canReconnect(true,false,false),"fresh activity has no stored target");
        check(w.explicitRequest(true,false,false,d)==null,"no target cannot connect");
        d.session(true);d.accept(84,100,100);d.session(false);
        w.select("watch-A","V8 A",d);check(d.bpm()==null&&d.receivedWall()==0,"selection clears BPM and timestamp, not only text");
        check(d.label(101).equals("No reading yet"),"selection clears ended status");
        check(w.canReconnect(true,false,false),"selected foreground idle can explicitly reconnect");
        check(w.explicitRequest(false,false,false,d)==null,"background request blocked");
        check(w.explicitRequest(true,true,false,d)==null,"scan in progress blocks request");
        check(w.explicitRequest(true,false,true,d)==null,"connecting/connected/cleanup handle blocks duplicate request");
        check(w.explicitRequest(true,false,false,d).equals("watch-A"),"explicit reconnect returns selected watch only");
        check(!d.current(101)&&d.bpm()==null,"connect alone cannot create/start a reading");
        w.leftForeground(true);check(w.disconnectedText().contains("leaving the app or screen sleep"),"foreground cause explained without guessing exact trigger");
        check(w.target().equals("watch-A"),"selection survives foreground exit only in this instance");
        check(w.canReconnect(true,false,false),"return enables manual action, does not execute it");
        check(d.bpm()==null&&!d.current(102),"return never starts acquisition");
        w.userDisconnected();check(w.disconnectedText().contains("by you"),"user disconnect distinguished");
        w.leftForeground(false);check(w.disconnectedText().contains("by you"),"later idle exit does not rewrite cause");
        w.explicitRequest(true,false,false,d);check(!w.disconnectedText().contains("by you"),"new attempt clears previous cause");
        check(w.disconnectedText().contains("Find my watch"),"failed attempt offers manual rescan");
        d.session(true);d.accept(79,200,200);d.session(false);
        w.explicitRequest(true,false,false,d);check(d.bpm()==null,"reconnect same watch clears stale value too");
        d.session(true);d.accept(80,300,300);
        w.select("watch-B","V8 B",d);check(w.target().equals("watch-B")&&d.bpm()==null&&!d.current(300),"switching watch clears full old session display");
        w.clear(d);check(w.target()==null&&!w.canReconnect(true,false,false),"model change forgets prior target");
        check(new WatchSelection<String>().target()==null,"activity recreation cannot restore device from storage");
        HrSessionTests.Fake f=new HrSessionTests.Fake(new V8Adapter());
        w.select("watch-A","A",d);w.explicitRequest(true,false,false,d);
        check(f.writes.isEmpty(),"connection selection does not issue measurement commands");
        f.session.start(35);f.complete(true);f.complete(true);f.session.stop("Disconnect");
        check(!w.canReconnect(true,false,true),"manual reconnect unavailable while cleanup holds connection");
        f.complete(true);f.complete(true);int n=f.writes.size();
        w.leftForeground(true);w.canReconnect(true,false,false);f.advance(60000);
        check(f.writes.size()==n,"cleanup/foreground return cannot queue automatic restart or repeat");
        w.explicitRequest(true,false,false,d);check(f.writes.size()==n,"reconnect policy cannot start HR");
        System.out.println("PASS: "+count+" synthetic selection/reconnect workflow assertions; no phone connection validation.");
    }
}
