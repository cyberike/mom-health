package org.momhealth.gateway.history;

import java.time.DateTimeException;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Original offline decoder of the SDK-documented HR record layouts; never sends bytes. */
public final class HrHistoryCodec {
    public enum Kind {
        STATIC(0x55,10), DYNAMIC(0x54,24);
        final int opcode, width;
        Kind(int opcode,int width) { this.opcode=opcode; this.width=width; }
    }
    public enum ReadMode { START, CONTINUE }
    public static final int MAX_PACKET_BYTES=480;
    public static final class Record {
        public final String model;
        public final Kind kind;
        /** Watch-local block time. No timezone/UTC or per-array sample times are inferred. */
        public final LocalDateTime deviceLocalTime;
        public final List<Integer> heartRates;
        public final List<String> missingReasons;
        private Record(String model,Kind kind,LocalDateTime time,List<Integer> rates,List<String> reasons) {
            this.model=model; this.kind=kind; this.deviceLocalTime=time;
            this.heartRates=Collections.unmodifiableList(rates);
            this.missingReasons=Collections.unmodifiableList(reasons);
        }
    }
    public static final class Page {
        public final List<Record> records;
        /** Deliberately no inferred end-of-history flag: source terminal framing is ambiguous. */
        public final String completion = "UNKNOWN";
        private Page(List<Record> records) { this.records=Collections.unmodifiableList(records); }
    }
    private HrHistoryCodec() {}

    public static Page decodeRecords(String model,Kind kind,byte[] packet) {
        HistoryCapabilityRegistry.forModel(model);
        if (kind==null || packet==null || packet.length==0 || packet.length>MAX_PACKET_BYTES)
            throw new IllegalArgumentException("Missing or oversized history packet");
        // The SDK treats short/trailing-FF frames as completion. Do not turn truncation into success.
        if (packet.length%kind.width!=0 || (packet[packet.length-1]&255)==255)
            throw new IllegalArgumentException("Ambiguous terminal or incomplete history frame; completion unverified");
        List<Record> out=new ArrayList<>();
        for(int base=0;base<packet.length;base+=kind.width) {
            if ((packet[base]&255)!=kind.opcode) throw new IllegalArgumentException("Wrong record opcode");
            LocalDateTime time;
            try {
                time=LocalDateTime.of(2000+bcd(packet[base+3]),bcd(packet[base+4]),bcd(packet[base+5]),
                    bcd(packet[base+6]),bcd(packet[base+7]),bcd(packet[base+8]));
            } catch(DateTimeException ex) { throw new IllegalArgumentException("Invalid watch-local date",ex); }
            List<Integer> rates=new ArrayList<>(); List<String> reasons=new ArrayList<>();
            for(int i=base+9;i<base+kind.width;i++) {
                int bpm=packet[i]&255;
                rates.add(bpm>=20 && bpm<=250 ? Integer.valueOf(bpm):null);
                reasons.add(bpm>=20 && bpm<=250 ? null : bpm==0 ? "no_result":"unknown");
            }
            out.add(new Record(model,kind,time,rates,reasons));
        }
        return new Page(out);
    }
    /** Offline review fixture only. No arbitrary mode or deletion API. */
    public static byte[] commandForReview(String model,Kind kind,ReadMode mode) {
        HistoryCapabilityRegistry.forModel(model);
        if(kind==null || mode==null) throw new IllegalArgumentException("Missing kind/mode");
        byte[] out=new byte[16]; out[0]=(byte)kind.opcode;
        out[1]=(byte)(mode==ReadMode.START?0:2);
        out[15]=(byte)((out[0]&255)+(out[1]&255));
        return out;
    }
    /** Exact previously observed watch-local record cursor only; never a phone-time guess. */
    public static byte[] commandForReview(String model,Kind kind,ReadMode mode,LocalDateTime exactStoredTime) {
        byte[] out=commandForReview(model,kind,mode);
        if(exactStoredTime==null)return out;
        if(exactStoredTime.getYear()<2000 || exactStoredTime.getYear()>2099 || exactStoredTime.getNano()!=0)
            throw new IllegalArgumentException("Cursor must be a whole-second watch date in 2000–2099");
        int[] values={exactStoredTime.getYear()-2000,exactStoredTime.getMonthValue(),exactStoredTime.getDayOfMonth(),
            exactStoredTime.getHour(),exactStoredTime.getMinute(),exactStoredTime.getSecond()};
        for(int i=0;i<values.length;i++)out[i+4]=(byte)((values[i]/10)*16+values[i]%10);
        int sum=0;for(int i=0;i<15;i++)sum+=out[i]&255;out[15]=(byte)sum;
        return out;
    }
    private static int bcd(byte value) {
        int v=value&255;
        if((v&15)>9 || (v>>>4)>9) throw new IllegalArgumentException("Invalid BCD date");
        return (v>>>4)*10+(v&15);
    }
}
