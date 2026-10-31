package org.momhealth.gateway.history;

import java.util.UUID;

/** Prepared synthetic tests. Do not execute until testing is authorized. */
public final class HistoryTransferPlanTest {
    private static void check(boolean condition) {if(!condition)throw new AssertionError();}
    private static byte[] record() {return new byte[]{0x55,0,0,0x26,0x10,0x22,0x12,0,0,80};}
    private static HistoryTransferPlan plan(UUID id) {
        HistoryTransferPlan p=new HistoryTransferPlan("V8","synthetic",id,HrHistoryCodec.Kind.STATIC);p.begin(0);return p;
    }
    public static void main(String[] args) {
        UUID id=UUID.randomUUID();HistoryTransferPlan p=plan(id);
        long start=p.pendingCommand().token;
        check(p.pendingCommand().token==start);
        byte[] cmd=p.pendingCommand().bytesForReview();cmd[0]=0;
        check(p.pendingCommand().bytesForReview()[0]==0x55);
        p.writeResult("synthetic",id,start,true,1);
        for(int i=0;i<50;i++)p.notification("synthetic",id,record(),2+i);
        check(p.snapshot().size()==50 && p.state()==HistoryTransferPlan.State.COMMAND_PENDING);
        check(p.pendingCommand().mode==HrHistoryCodec.ReadMode.CONTINUE);
        long next=p.pendingCommand().token;
        p.writeResult("synthetic",id,start,false,52);
        check(p.pendingCommand().token==next); // stale confirmation cannot kill next write
        p.writeResult("synthetic",id,next,true,53);
        p.notification("synthetic",id,new byte[]{0x55,(byte)255},54);
        check(p.state()==HistoryTransferPlan.State.TERMINAL_AMBIGUOUS && p.snapshot().size()==50);
        p.notification("synthetic",id,record(),55);check(p.snapshot().size()==50);
        HistoryTransferPlan timed=plan(id);timed.tick(10000);
        check(timed.state()==HistoryTransferPlan.State.TIMED_OUT && timed.pendingCommand()==null);
        HistoryTransferPlan failed=plan(id);failed.writeResult("synthetic",id,failed.pendingCommand().token,false,1);
        check(failed.state()==HistoryTransferPlan.State.FAILED);
        HistoryTransferPlan early=plan(id);early.notification("synthetic",id,record(),1);
        check(early.state()==HistoryTransferPlan.State.FAILED);
        HistoryTransferPlan cancelled=plan(id);cancelled.cancel();
        check(cancelled.state()==HistoryTransferPlan.State.CANCELLED && cancelled.pendingCommand()==null);
        HistoryTransferPlan foreign=plan(id);
        try { foreign.notification("other",id,record(),1);throw new AssertionError(); }
        catch(IllegalArgumentException expected) {check(foreign.state()==HistoryTransferPlan.State.COMMAND_PENDING);}
        foreign.disconnected("synthetic",id);check(foreign.state()==HistoryTransferPlan.State.FAILED);
        HistoryTransferPlan bounded=plan(id);
        for(int window=0;window<4;window++) {
            bounded.writeResult("synthetic",id,bounded.pendingCommand().token,true,window*100);
            for(int j=0;j<50;j++)bounded.notification("synthetic",id,record(),window*100+j+1);
        }
        check(bounded.state()==HistoryTransferPlan.State.LIMIT_REACHED && bounded.snapshot().size()==200);
        bounded.discard();check(bounded.snapshot().isEmpty());
    }
}
