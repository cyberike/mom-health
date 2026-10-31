package org.momhealth.gateway;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** Separates accepted local observations from potentially slow network transfers. */
final class CaregiverWorkQueues {
    private static ThreadPoolExecutor lane(int capacity) {
        return new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(capacity));
    }
    private final ThreadPoolExecutor capture = lane(256);
    private final ThreadPoolExecutor upload = lane(1);
    void capture(Runnable action) throws RejectedExecutionException { capture.execute(action); }
    void upload(Runnable action) throws RejectedExecutionException { upload.execute(action); }
    void shutdown() { capture.shutdown(); upload.shutdown(); }
    boolean awaitTermination(long timeout, TimeUnit units) throws InterruptedException {
        long remaining = units.toNanos(timeout);
        long started = System.nanoTime();
        if (!capture.awaitTermination(remaining, TimeUnit.NANOSECONDS)) return false;
        remaining = Math.max(0, remaining - (System.nanoTime() - started));
        return upload.awaitTermination(remaining, TimeUnit.NANOSECONDS);
    }
}
