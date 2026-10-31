import org.momhealth.gateway.*;
public final class ReadingDisplayTests {
    static int count;
    static void check(boolean ok,String why){count++;if(!ok)throw new AssertionError(why);}
    public static void main(String[] args){
        ReadingDisplay d=new ReadingDisplay();
        check(d.bpm()==null && !d.current(0),"empty display has no current BPM");
        check(d.label(0).equals("No reading yet"),"first launch does not imply an earlier reading");
        d.session(false);check(d.label(0).equals("No reading yet"),"disconnected before first measurement stays empty");
        d.accept(80,1000,10);check(d.bpm()==null,"idle notification cannot populate display");
        d.session(true);check(d.label(10).equals("Waiting for a reading"),"new session waits");
        d.accept(82,2000,20);check(d.bpm()==82 && d.receivedWall()==2000,"accepted reading and timestamp");
        check(d.current(20),"current at receipt");check(d.current(5019),"current just before freshness boundary");
        check(!d.current(5020) && d.label(5020).startsWith("Stale"),"stale at five seconds");
        check(!d.current(19),"monotonic regression cannot be current");
        d.session(true);check(d.current(21),"duplicate active callback preserves reading");
        d.accept(0,3000,30);check(d.bpm()==82 && d.receivedWall()==2000,"zero does not replace or freshen value");
        d.accept(251,3000,30);check(d.bpm()==82,"invalid high value rejected");
        d.accept(81,4000,6000);check(d.current(6000)&&d.bpm()==81,"new packet recovers from stale");
        d.session(false);check(!d.current(6001)&&d.label(6001).startsWith("Measurement ended"),"Stop/timeout/disconnect invalidates current immediately");
        check(d.bpm()==81 && d.receivedWall()==4000,"last value/time retained explicitly");
        d.session(false);check(d.label(6002).startsWith("Measurement ended"),"duplicate idle keeps ended status");
        d.accept(90,5000,6003);check(d.bpm()==81,"late reading cannot replace ended reading");
        d.session(true);check(!d.current(6004)&&d.label(6004).contains("previous measurement"),"new session cannot relabel prior value current");
        d.accept(84,6000,7000);check(d.current(7000)&&d.bpm()==84,"new session requires new accepted reading");
        d.reset();check(d.bpm()==null&&d.receivedWall()==0&&!d.current(7001),"new watch/model clears all prior data");
        d.session(true);d.session(false);check(d.bpm()==null&&d.label(0).startsWith("Measurement ended"),"empty session ends without manufactured BPM");
        check(d.label(0).equals("Measurement ended - no reading received"),"empty ended session never claims a last reading");
        d.session(true);d.accept(0,100,100);d.session(false);
        check(d.label(101).equals("Measurement ended - no reading received"),"zero-only session has no last reading");
        d.reset();check(d.label(102).equals("No reading yet"),"model/watch reset restores empty wording");
        for(int value=0;value<256;value++){
            d.reset();d.session(true);d.accept(value,100,100);
            check((d.bpm()!=null)==(value>=20&&value<=250),"display bounds");
            d.session(false);check(!d.current(101),"all ended values remain noncurrent");
        }
        System.out.println("PASS: "+count+" synthetic presentation assertions; no Android rendering or hardware validation.");
    }
}
