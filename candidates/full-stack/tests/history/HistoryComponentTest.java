package org.momhealth.gateway.history;

import java.util.UUID;

/** Synthetic test source prepared only; not executed under build-only authorization. */
public final class HistoryComponentTest {
    private static int checks;
    private static void check(boolean ok) { checks++; if(!ok) throw new AssertionError("check "+checks); }
    private static void rejects(Runnable action) {
        boolean rejected=false;
        try { action.run(); } catch(IllegalArgumentException|IllegalStateException|UnsupportedOperationException expected) { rejected=true; }
        check(rejected);
    }
    private static byte[] record(int opcode,int width,int bpm) {
        byte[] p=new byte[width];p[0]=(byte)opcode;
        p[3]=0x26;p[4]=0x10;p[5]=0x22;p[6]=0x12;p[7]=0x34;p[8]=0x56;
        for(int i=9;i<width;i++) p[i]=(byte)bpm;
        return p;
    }
    public static void main(String[] args) {
        for(String model:new String[]{"V1","V4","V8"}) {
            check(!HistoryCapabilityRegistry.forModel(model).historyAvailable);
            rejects(()->HistoryCapabilityRegistry.forModel(model).requireHardwareHistory());
            for(HrHistoryCodec.Kind kind:HrHistoryCodec.Kind.values()) {
                byte[] p=record(kind.opcode,kind.width,80);
                HrHistoryCodec.Page page=HrHistoryCodec.decodeRecords(model,kind,p);
                check(page.records.size()==1 && "UNKNOWN".equals(page.completion));
                check(page.records.get(0).deviceLocalTime.toString().equals("2026-10-22T12:34:56"));
                check(page.records.get(0).heartRates.get(0)==80);
                rejects(()->page.records.clear());
                rejects(()->page.records.get(0).heartRates.clear());
                byte[] cmd=HrHistoryCodec.commandForReview(model,kind,HrHistoryCodec.ReadMode.CONTINUE);
                check(cmd.length==16 && cmd[1]==2 && (cmd[15]&255)==kind.opcode+2);
                check(HrHistoryCodec.commandForReview(model,kind,HrHistoryCodec.ReadMode.START)[1]==0);
                byte[] dated=HrHistoryCodec.commandForReview(model,kind,HrHistoryCodec.ReadMode.START,page.records.get(0).deviceLocalTime);
                check(dated[4]==0x26 && dated[5]==0x10 && dated[6]==0x22 && dated[9]==0x56);
                int checksum=0;for(int n=0;n<15;n++)checksum+=dated[n]&255;
                check((dated[15]&255)==(checksum&255));
                p[9]=0;check(HrHistoryCodec.decodeRecords(model,kind,p).records.get(0).heartRates.get(0)==null);
                p[9]=19;check("unknown".equals(HrHistoryCodec.decodeRecords(model,kind,p).records.get(0).missingReasons.get(0)));
                p[4]=0x1a;rejects(()->HrHistoryCodec.decodeRecords(model,kind,p));
                p[4]=0x02;p[5]=0x30;rejects(()->HrHistoryCodec.decodeRecords(model,kind,p));
                rejects(()->HrHistoryCodec.decodeRecords(model,kind,new byte[]{(byte)kind.opcode,(byte)255}));
                rejects(()->HrHistoryCodec.decodeRecords(model,kind,new byte[481]));
                rejects(()->HrHistoryCodec.decodeRecords(model,kind,record(0x13,kind.width,80)));
                byte[] last=record(kind.opcode,kind.width,80);last[last.length-1]=(byte)255;
                rejects(()->HrHistoryCodec.decodeRecords(model,kind,last));
            }
        }
        check(!HistoryCapabilityRegistry.forModel("V4").liveAvailable);
        rejects(()->HistoryCapabilityRegistry.forModel("Other"));
        UUID id=UUID.randomUUID();
        HistoryReviewSession s=new HistoryReviewSession("V8","synthetic-device",id,HrHistoryCodec.Kind.STATIC);
        byte[] p=record(0x55,10,80);
        rejects(()->s.accept("other-device",id,p));
        rejects(()->s.accept("synthetic-device",UUID.randomUUID(),p));
        for(int i=0;i<50;i++) s.accept("synthetic-device",id,p);
        check(s.snapshot().size()==50); // identical values are not assumed duplicate identities
        rejects(()->s.accept("synthetic-device",id,p));
        s.close();check(s.isClosed() && s.snapshot().isEmpty());
        rejects(()->s.accept("synthetic-device",id,p));
        HistoryReviewSession limited=new HistoryReviewSession("V8","synthetic-device",id,HrHistoryCodec.Kind.STATIC);
        byte[] many=new byte[480];
        for(int i=0;i<48;i++) System.arraycopy(p,0,many,i*10,10);
        for(int i=0;i<10;i++) limited.accept("synthetic-device",id,many);
        check(limited.snapshot().size()==480);
        rejects(()->limited.accept("synthetic-device",id,many));
        check(limited.snapshot().size()==480);
        byte[] malformed=many.clone();malformed[470]=0x13;
        rejects(()->limited.accept("synthetic-device",id,malformed));
        check(limited.snapshot().size()==480);
        System.out.println(checks+" synthetic history checks passed");
    }
}
