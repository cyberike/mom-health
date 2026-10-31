package org.momhealth.gateway.data;
import android.content.Context;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.*;
import java.time.Instant;
import java.util.*;

/** Lazy, app-private persistence. Recording consent is off on every new instance/process. */
public final class HealthStore extends SQLiteOpenHelper {
    public static final int CAPACITY=10000;
    private boolean recordingEnabled;
    public HealthStore(Context context) { super(context.getApplicationContext(),"mom-health-candidate.db",null,1); }
    public synchronized void setRecordingEnabled(boolean enabled) { recordingEnabled=enabled; }
    public synchronized boolean isRecordingEnabled() { return recordingEnabled; }
    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE samples (row_id INTEGER PRIMARY KEY AUTOINCREMENT,sample_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,uploaded INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,last_attempt TEXT,last_error TEXT)");
        db.execSQL("CREATE INDEX pending_samples ON samples(uploaded,row_id)");
    }
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion) { throw new IllegalStateException("Explicit migration required"); }
    /** Returns false for exact duplicate; throws for conflict/full/consent missing. */
    public synchronized boolean append(HealthSample sample) {
        if(!recordingEnabled) throw new IllegalStateException("Local recording is off");
        if(sample==null) throw new IllegalArgumentException("Sample required");
        SQLiteDatabase db=getWritableDatabase(); db.beginTransaction();
        try {
            try(Cursor c=db.rawQuery("SELECT payload FROM samples WHERE sample_id=?",new String[]{sample.sampleId})) {
                if(c.moveToFirst()) { if(!c.getString(0).equals(sample.toJson().toString())) throw new IllegalArgumentException("Sample ID content conflict"); db.setTransactionSuccessful(); return false; }
            }
            if(count(db,"1=1")>=CAPACITY) throw new IllegalStateException("Local capacity reached; explicitly clear or delete records");
            ContentValues v=new ContentValues(); v.put("sample_id",sample.sampleId); v.put("payload",sample.toJson().toString()); db.insertOrThrow("samples",null,v);
            db.setTransactionSuccessful(); return true;
        } finally { db.endTransaction(); }
    }
    public static final class HistoryEntry {
        public final long rowId; public final HealthSample sample; public final boolean uploaded; public final int attempts; public final String lastError;
        private HistoryEntry(Cursor c) { rowId=c.getLong(0); sample=HealthSample.fromJson(c.getString(1)); uploaded=c.getInt(2)!=0; attempts=c.getInt(3); lastError=c.isNull(4)?null:c.getString(4); }
    }
    public synchronized List<HistoryEntry> history(int limit,long beforeRowId) {
        limit(limit); List<HistoryEntry> result=new ArrayList<>();
        try(Cursor c=getReadableDatabase().rawQuery("SELECT row_id,payload,uploaded,attempts,last_error FROM samples WHERE row_id<? ORDER BY row_id DESC LIMIT ?",new String[]{Long.toString(beforeRowId<=0?Long.MAX_VALUE:beforeRowId),Integer.toString(limit)})) {
            while(c.moveToNext()) result.add(new HistoryEntry(c));
        } return Collections.unmodifiableList(result);
    }
    public synchronized UploadBatch pendingBatch(int limit) {
        limit(limit); List<HealthSample> values=new ArrayList<>();
        try(Cursor c=getReadableDatabase().rawQuery("SELECT payload FROM samples WHERE uploaded=0 ORDER BY row_id LIMIT ?",new String[]{Integer.toString(limit)})) { while(c.moveToNext()) values.add(HealthSample.fromJson(c.getString(0))); }
        return new UploadBatch(values);
    }
    /** Caregiver automation uploads only its configured opaque watch identity. */
    public synchronized UploadBatch pendingBatchForDevice(int limit,String deviceId) {
        limit(limit);if(deviceId==null)throw new IllegalArgumentException("Device required");
        List<HealthSample> values=new ArrayList<>();
        try(Cursor c=getReadableDatabase().rawQuery("SELECT payload FROM samples WHERE uploaded=0 ORDER BY row_id LIMIT ?",new String[]{Integer.toString(CAPACITY)})){
            while(c.moveToNext()&&values.size()<limit){HealthSample sample=HealthSample.fromJson(c.getString(0));if(deviceId.equals(sample.deviceId))values.add(sample);}
        }
        return new UploadBatch(values);
    }
    private static void limit(int n) { if(n<1||n>100) throw new IllegalArgumentException("Limit must be 1..100"); }
    private static long count(SQLiteDatabase db,String where) { try(Cursor c=db.rawQuery("SELECT COUNT(*) FROM samples WHERE "+where,null)) { c.moveToFirst();return c.getLong(0); } }
    public static final class Status {
        public final long pending,uploaded,failed; public final String lastAttempt;
        private Status(long p,long u,long f,String t) { pending=p;uploaded=u;failed=f;lastAttempt=t; }
    }
    public synchronized Status status() {
        SQLiteDatabase db=getReadableDatabase(); String time=null;
        try(Cursor c=db.rawQuery("SELECT MAX(last_attempt) FROM samples",null)) { if(c.moveToFirst()&&!c.isNull(0)) time=c.getString(0); }
        return new Status(count(db,"uploaded=0"),count(db,"uploaded=1"),count(db,"uploaded=0 AND last_error IS NOT NULL"),time);
    }
    synchronized void beginAttempt(UploadBatch batch) {
        SQLiteDatabase db=getWritableDatabase(); db.beginTransaction();
        try { for(HealthSample s:batch.samples) db.execSQL("UPDATE samples SET attempts=attempts+1,last_attempt=?,last_error=NULL WHERE sample_id=? AND payload=? AND uploaded=0",new Object[]{Instant.now().toString(),s.sampleId,s.toJson().toString()}); db.setTransactionSuccessful(); }
        finally { db.endTransaction(); }
    }
    public synchronized void acknowledge(UploadBatch batch,BatchAck ack) {
        if(!batch.batchId.equals(ack.batchId)) throw new IllegalArgumentException("Batch mismatch");
        SQLiteDatabase db=getWritableDatabase(); db.beginTransaction();
        try { for(HealthSample s:batch.samples) {
            ContentValues v=new ContentValues();
            if(ack.committedIds.contains(s.sampleId)) { v.put("uploaded",1); v.putNull("last_error"); }
            else v.put("last_error",ack.rejected.containsKey(s.sampleId)?"Server rejected item":"Unacknowledged item; retry explicitly");
            // Payload comparison prevents an old response from acknowledging a replacement after local deletion.
            db.update("samples",v,"sample_id=? AND payload=? AND uploaded=0",new String[]{s.sampleId,s.toJson().toString()});
        } db.setTransactionSuccessful(); } finally { db.endTransaction(); }
    }
    synchronized void failed(UploadBatch batch,String reason) {
        SQLiteDatabase db=getWritableDatabase(); db.beginTransaction();
        try { ContentValues v=new ContentValues(); v.put("last_error",reason);
            for(HealthSample s:batch.samples) db.update("samples",v,"sample_id=? AND payload=? AND uploaded=0",new String[]{s.sampleId,s.toJson().toString()});
            db.setTransactionSuccessful(); } finally { db.endTransaction(); }
    }
    /** Explicit local deletion only; caller must ask the user and cancel an in-flight upload first. */
    public synchronized void clearAll() { getWritableDatabase().delete("samples",null,null); }
    public synchronized void deleteSample(String sampleId) { getWritableDatabase().delete("samples","sample_id=?",new String[]{sampleId}); }
    /** Explicit caregiver retention opt-in only. Never deletes pending/unacknowledged records. */
    public synchronized int pruneUploadedTo(int keepMostRecent) {
        if(keepMostRecent<100||keepMostRecent>CAPACITY)throw new IllegalArgumentException("Retention must keep 100..10000 uploaded records");
        SQLiteDatabase db=getWritableDatabase();db.beginTransaction();
        try {
            int removed=db.delete("samples","uploaded=1 AND row_id IN (SELECT row_id FROM samples WHERE uploaded=1 ORDER BY row_id DESC LIMIT -1 OFFSET ?)",new String[]{Integer.toString(keepMostRecent)});
            db.setTransactionSuccessful();return removed;
        } finally {db.endTransaction();}
    }
    public synchronized int pruneUploadedTo(int keepMostRecent,String deviceId){
        if(keepMostRecent<100||keepMostRecent>CAPACITY||deviceId==null)throw new IllegalArgumentException("Valid retention and device required");
        SQLiteDatabase db=getWritableDatabase();db.beginTransaction();
        try{
            List<String> remove=new ArrayList<>();int seen=0;
            try(Cursor c=db.rawQuery("SELECT payload FROM samples WHERE uploaded=1 ORDER BY row_id DESC LIMIT ?",new String[]{Integer.toString(CAPACITY)})){
                while(c.moveToNext()){HealthSample sample=HealthSample.fromJson(c.getString(0));if(deviceId.equals(sample.deviceId)&&++seen>keepMostRecent)remove.add(sample.sampleId);}
            }
            int removed=0;for(String id:remove)removed+=db.delete("samples","sample_id=? AND uploaded=1",new String[]{id});
            db.setTransactionSuccessful();return removed;
        }finally{db.endTransaction();}
    }
}
