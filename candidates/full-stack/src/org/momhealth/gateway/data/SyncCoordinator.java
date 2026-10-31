package org.momhealth.gateway.data;
import java.io.IOException;
import java.util.concurrent.CancellationException;

/** Caller invokes from its foreground executor, cancels uploader at Activity exit. */
public final class SyncCoordinator {
    private SyncCoordinator() { }
    public static BatchAck uploadOnce(HealthStore store,BatchUploader uploader) throws IOException {
        return uploadOnce(store,uploader,null);
    }
    public static BatchAck uploadOnce(HealthStore store,BatchUploader uploader,String deviceId) throws IOException {
        UploadBatch batch=deviceId==null?store.pendingBatch(100):store.pendingBatchForDevice(100,deviceId);
        if(batch.samples.isEmpty()) return null;
        store.beginAttempt(batch);
        try {
            BatchAck ack=uploader.upload(batch);
            if(uploader.isCancelled()) throw new CancellationException("Cancelled before local acknowledgment");
            uploader.acknowledgeIfActive(store,batch,ack); return ack;
        } catch(IOException|RuntimeException ex) {
            store.failed(batch,uploader.isCancelled()?"Upload cancelled; retry explicitly":"Upload failed; retry explicitly"); throw ex;
        }
    }
}
