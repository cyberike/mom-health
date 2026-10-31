package org.momhealth.gateway;

import android.bluetooth.*;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import java.util.ArrayDeque;
import java.util.UUID;

/** Foreground-only transport. One outstanding GATT operation; no pairing or reconnection. */
public final class BleController {
    public interface Listener {
        void event(String message);
        void state(boolean ready, boolean measuring);
        void reading(DeviceAdapter.Reading reading, String raw, long receivedAt);
        default void measurementFinished(boolean cleanupConfirmed) { }
    }
    private final Context context;
    private final Listener listener;
    private final DeviceAdapter adapter;
    private final java.util.concurrent.atomic.AtomicLong receivedEvents=new java.util.concurrent.atomic.AtomicLong();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final HrSession session;
    private final ArrayDeque<Operation> queue = new ArrayDeque<>();
    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic write, notify;
    private boolean ready, closing, awaitingMtu, awaitingSubscription;
    private int negotiatedMtu=23;
    private Operation pending;
    private Runnable operationTimeout, connectionTimeout;
    private static final UUID CCC = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final class Operation {
        final byte[] value; final String label; final HrSession.Completion done;
        Operation(byte[] value, String label, HrSession.Completion done) { this.value=value.clone(); this.label=label; this.done=done; }
    }
    public BleController(Context context, DeviceAdapter adapter, Listener listener) {
        this.context=context; this.adapter=adapter; this.listener=listener;
        session=new HrSession(adapter,new HrSession.Driver() {
            public long now() {return SystemClock.elapsedRealtime();}
            public void write(byte[] bytes,String label,HrSession.Completion done) {enqueue(bytes,label,done);}
            public void later(long delay,Runnable action) {handler.postDelayed(action,delay);}
            public void event(String text) {listener.event(text);}
            public void changed() {update();}
            public void finished(boolean confirmed) {listener.measurementFinished(confirmed);if(closing || !confirmed)close();}
        });
    }
    public boolean hasConnection() { return gatt!=null; }
    public boolean measurementConfirmed(){return session.stage()==HrSession.Stage.ACTIVE;}
    public boolean readyForMeasurement(){return ready && !closing && !session.busy() && pending==null && queue.isEmpty();}
    public String statusText() {
        switch(session.stage()) {
            case ENABLING:return "Preparing V8 reporting (up to 5 seconds)";
            case STARTING:return "Starting HR - 30-second deadline running";
            case ACTIVE:return "HR active - 30-second maximum";
            case CLEANUP:return "Stopping HR / disabling reporting - awaiting confirmation";
            default:return ready?"Connected - ready":"Disconnected / connecting";
        }
    }
    public void connect(BluetoothDevice device) {
        if (gatt != null) { listener.event("Disconnect the current session first."); return; }
        if(!WatchConnectionLease.acquire(this)){listener.event("Another manual or caregiver session owns the watch connection.");update();return;}
        closing=false; ready=false; negotiatedMtu=23; awaitingMtu=false; awaitingSubscription=false;
        listener.event("Connecting to selected " + adapter.model() + " device…"); update();
        try {
            gatt=device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE);
            if(gatt==null) { fail("Android could not create a GATT connection."); return; }
            update();
            connectionTimeout=() -> fail("Connection/service discovery timed out.");
            handler.postDelayed(connectionTimeout, 15000);
        } catch (SecurityException | IllegalArgumentException e) { fail("Bluetooth permission/connection unavailable."); }
    }
    public void readIdentity() {
        if(!ready || closing || session.busy() || pending!=null || !queue.isEmpty()) return;
        enqueue(adapter.firmwareRequest(),"Read firmware",null);
        enqueue(adapter.batteryRequest(),"Read battery",null);
    }
    public void startHeartRate() {
        if(!ready || closing || session.busy() || pending!=null || !queue.isEmpty()) {
            listener.event("Wait for current writes/cleanup to finish before starting HR.");return;
        }
        session.start(negotiatedMtu);
    }
    public void stopHeartRate() {session.stop("User Stop");}
    public void disconnect() {
        if(gatt==null || closing)return;
        closing=true;update();
        if(session.busy())session.stop("Leaving foreground or disconnect requested"); else close();
    }
    private void update() {listener.state(ready && !closing && !session.busy(),session.collecting());}
    private void enqueue(byte[] bytes,String label,HrSession.Completion done) {
        if(!adapter.permits(bytes))throw new IllegalArgumentException("Command outside allowlist");
        queue.add(new Operation(bytes,label,done));pump();
    }
    private void completeWrite(boolean success) {
        cancel(operationTimeout);
        Operation completed=pending;pending=null;
        if(completed==null)return;
        listener.event(completed.label+" GATT write "+(success?"confirmed":"failed/rejected"));
        if(completed.done!=null)completed.done.complete(success);
        else if(!success) {fail("Identification write failed.");return;}
        pump();
    }
    @SuppressWarnings("deprecation")
    private void pump() {
        if(pending!=null || queue.isEmpty() || gatt==null || write==null) return;
        pending=queue.remove();
        if(!adapter.permits(pending.value)) {fail("Rejected command."); return;}
        try {
            write.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
            write.setValue(pending.value);
            if(!gatt.writeCharacteristic(write)) {completeWrite(false);return;}
            listener.event("TX " + pending.label + ": " + V1Adapter.hex(pending.value));
            operationTimeout=() -> fail("GATT write timed out. Queue quarantined: callback identity is uncertain; cleanup delivery unconfirmed.");
            handler.postDelayed(operationTimeout,5000);
        } catch(SecurityException e) {fail("Bluetooth permission revoked; cleanup cannot be delivered.");}
    }
    private void cancel(Runnable r) { if(r!=null) handler.removeCallbacks(r); }
    private void fail(String message) {
        listener.event(message);
        queue.clear();pending=null;cancel(operationTimeout);
        session.transportLost(message);close();
    }
    private void close() {
        cancel(connectionTimeout);cancel(operationTimeout);
        queue.clear();pending=null;ready=false;closing=false;awaitingMtu=false;awaitingSubscription=false;
        BluetoothGatt old=gatt; gatt=null; write=null; notify=null;
        if(old!=null) {
            try {old.disconnect();} catch(SecurityException ignored) {}
              try {old.close();} finally {WatchConnectionLease.release(this);}
        }
          else WatchConnectionLease.release(this);
          update(); listener.event("Disconnected.");
    }
    private void post(BluetoothGatt origin,Runnable task) {
        handler.post(() -> {if(origin==gatt) task.run();});
    }
    private final BluetoothGattCallback callback=new BluetoothGattCallback() {
        @Override public void onConnectionStateChange(BluetoothGatt g,int status,int state) {
            post(g,() -> {
                if(status!=BluetoothGatt.GATT_SUCCESS || state==BluetoothProfile.STATE_DISCONNECTED) {
                    fail("Link ended (GATT " + status + "). Any active stop is unconfirmed; check the watch."); return;
                }
                if(state==BluetoothProfile.STATE_CONNECTED && !closing) {
                    try {if(!g.discoverServices()) fail("Service discovery could not start.");}
                    catch(SecurityException e) {fail("Bluetooth permission unavailable.");}
                }
            });
        }
        @Override @SuppressWarnings("deprecation") public void onServicesDiscovered(BluetoothGatt g,int status) {
            post(g,() -> {
                if(closing) return;
                if(status!=BluetoothGatt.GATT_SUCCESS) {fail("Service discovery failed: " + status); return;}
                BluetoothGattService s=g.getService(adapter.service());
                if(s==null) {fail("Expected "+adapter.model()+" service absent. Do not assume another model is compatible.");return;}
                write=s.getCharacteristic(adapter.writeCharacteristic()); notify=s.getCharacteristic(adapter.notifyCharacteristic());
                if(write==null || notify==null || (write.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE)==0
                        || (notify.getProperties() & BluetoothGattCharacteristic.PROPERTY_NOTIFY)==0) {
                    fail("Expected write-with-response / notification properties absent.");return;
                }
                if(adapter.needsReporting()) {
                    awaitingMtu=true;
                    try {if(!g.requestMtu(64))fail("MTU negotiation could not start; no streaming enabled.");}
                    catch(SecurityException e) {fail("MTU permission unavailable.");}
                } else subscribe(g);
            });
        }
        @Override public void onMtuChanged(BluetoothGatt g,int mtu,int status) {
            post(g,() -> {
                if(!awaitingMtu || closing)return;
                awaitingMtu=false;
                if(status!=BluetoothGatt.GATT_SUCCESS || mtu<adapter.minimumMtu()) {
                    fail("MTU insufficient/unconfirmed for complete V8 activity frames; no streaming enabled.");return;
                }
                negotiatedMtu=mtu;listener.event("Negotiated ATT MTU="+mtu+"; required >=35 for V8 32-byte notifications.");subscribe(g);
            });
        }
        @Override public void onDescriptorWrite(BluetoothGatt g,BluetoothGattDescriptor descriptor,int status) {
            post(g,() -> {
                if(!CCC.equals(descriptor.getUuid()) || closing || !awaitingSubscription) return;
                awaitingSubscription=false;
                if(status!=BluetoothGatt.GATT_SUCCESS) {fail("Notification subscription rejected: " + status);return;}
                cancel(connectionTimeout); ready=true; update();
                listener.event("Connected and subscribed. Tap Read battery / firmware, then Start HR.");
            });
        }
        @Override public void onCharacteristicWrite(BluetoothGatt g,BluetoothGattCharacteristic c,int status) {
            post(g,() -> {
                if(!adapter.writeCharacteristic().equals(c.getUuid()) || pending==null) return;
                completeWrite(status==BluetoothGatt.GATT_SUCCESS);
            });
        }
        @Override @SuppressWarnings("deprecation") public void onCharacteristicChanged(BluetoothGatt g,BluetoothGattCharacteristic c) {
            byte[] value=c.getValue(); if(value!=null) receive(g,c.getUuid(),value.clone(),"legacy");
        }
        @Override public void onCharacteristicChanged(BluetoothGatt g,BluetoothGattCharacteristic c,byte[] value) {
            if(value!=null) receive(g,c.getUuid(),value.clone(),"value");
        }
    };
    @SuppressWarnings("deprecation")
    private void subscribe(BluetoothGatt g) {
        BluetoothGattDescriptor descriptor=notify.getDescriptor(CCC);
        if(descriptor==null) {fail("Notification descriptor absent.");return;}
        try {
            if(!g.setCharacteristicNotification(notify,true)) {fail("Notification registration failed.");return;}
            awaitingSubscription=true;
            descriptor.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
            if(!g.writeDescriptor(descriptor))fail("Notification subscription failed.");
        } catch(SecurityException e) {fail("Bluetooth permission unavailable.");}
    }
    private void receive(BluetoothGatt origin,UUID characteristic,byte[] bytes,String callbackRoute) {
        if(!adapter.notifyCharacteristic().equals(characteristic)) return;
        final long eventId=receivedEvents.incrementAndGet();
        long token=session.token();
        long arrived=SystemClock.elapsedRealtime(), wall=System.currentTimeMillis();
        post(origin,() -> {
            if(!ready || closing) return;
            // Activity frames also contain unrelated metrics: do not expose those fields.
            String raw=adapter.needsReporting() && bytes.length>0 && bytes[0]==9
                ? "09 activity len="+bytes.length+" HR-byte21="+(V8Adapter.supportedActivityLength(bytes.length)?(bytes[21]&255):"unavailable")+" (other fields omitted)"
                : V1Adapter.hex(bytes);
            DeviceAdapter.Reading r=adapter.decode(bytes);
            String received=new java.text.SimpleDateFormat("HH:mm:ss.SSS",java.util.Locale.ROOT).format(new java.util.Date(wall));
            String phase=session.phase(token,arrived);
            listener.event("RX event="+eventId+" callback="+callbackRoute+" at "+received+" len="+bytes.length+" session="+token+" ["+phase+"] "+r.kind+": "+raw);
            if(r.kind==DeviceAdapter.Reading.Kind.HEART_RATE) {
                if(session.accepts(token,arrived,r)) listener.reading(r,raw,wall);
                else listener.event("HR candidate excluded: "+phase+". Not assigned to a current measurement.");
            } else if(r.kind==DeviceAdapter.Reading.Kind.NO_RESULT) {
                listener.event(r.text);
                if("inside confirmed request".equals(phase))listener.reading(r,raw,wall);
            } else if(r.kind==DeviceAdapter.Reading.Kind.BATTERY || r.kind==DeviceAdapter.Reading.Kind.FIRMWARE) listener.reading(r,raw,wall);
            else listener.event(r.text==null?"Unclassified response":r.text);
        });
    }
}
