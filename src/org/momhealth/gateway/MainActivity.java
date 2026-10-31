package org.momhealth.gateway;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.content.res.ColorStateList;
import android.view.View;
import android.widget.*;
import java.text.SimpleDateFormat;
import java.util.*;

public final class MainActivity extends Activity implements BleController.Listener {
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final Map<String,BluetoothDevice> devices=new LinkedHashMap<>();
    private final ArrayDeque<String> events=new ArrayDeque<>();
    private BleController controller;
    private DeviceAdapter chosenAdapter;
    private final WatchSelection<BluetoothDevice> watch=new WatchSelection<>();
    private Button modelButton;
    private BluetoothLeScanner scanner;
    private TextView status,battery,firmware,heart,log,selected,readingState,received,notice;
    private final ReadingDisplay display=new ReadingDisplay();
    private LinearLayout details;
    private boolean measuringNow;
    private final ScreenAwakeState awake=new ScreenAwakeState();
    private final Runnable refresh=new Runnable(){public void run(){if(visible){renderReading();handler.postDelayed(this,1000);}}};
    private Button scan,read,start,disconnect,reconnect;
    private LinearLayout deviceRows;
    private boolean scanning,visible,connected;
    private int scanGeneration;
    private ScanCallback scanCallback;
    private Runnable scanTimeout;
    private final SimpleDateFormat timestamp=new SimpleDateFormat("HH:mm:ss.SSS",Locale.ROOT);
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        controller=new BleController(this,new V1Adapter(),this);
        getWindow().setStatusBarColor(Color.rgb(15,17,24));getWindow().setNavigationBarColor(Color.rgb(15,17,24));
        getWindow().getDecorView().setSystemUiVisibility(0);
        ScrollView scroll=new ScrollView(this);
        scroll.setOnApplyWindowInsetsListener((view,insets) -> {
            android.graphics.Insets bars=insets.getInsets(android.view.WindowInsets.Type.systemBars() | android.view.WindowInsets.Type.displayCutout());
            view.setPadding(bars.left,bars.top,bars.right,bars.bottom); return insets;
        });
        LinearLayout page=new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL); page.setPadding(dp(20),dp(20),dp(20),dp(24));
        page.setBackgroundColor(Color.rgb(15,17,24));scroll.setBackgroundColor(Color.rgb(15,17,24));scroll.addView(page);setContentView(scroll);
        LinearLayout header=card(page,0xff25375b,0xff393051);
        text(header,"MOM HEALTH",16).setTextColor(0xffc9d8ff);
        text(header,"A moment for you",28).setTypeface(Typeface.DEFAULT,Typeface.BOLD);
        text(header,"Watch-reported heart rate",17);
        LinearLayout hr=card(page,0xff38252e,0xff20222c);
        text(hr,"\u2665  HEART RATE",16).setTextColor(0xffffa6ae);
        heart=text(hr,"-- bpm",56);heart.setTypeface(Typeface.DEFAULT,Typeface.BOLD);
        readingState=text(hr,"Not measuring",19);readingState.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        received=text(hr,"No reading received",16);received.setTextColor(0xffccd0df);
        start=button(hr,"Measure heart rate - 30 seconds",v -> {
            if(measuringNow)controller.stopHeartRate();else {notice.setText("Up to 30 seconds. Keep the app open. Accuracy is not independently verified.");controller.startHeartRate();}
        });
        start.setTextColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled},new int[]{}},new int[]{0xffbfc3d0,0xff21151b}));
        start.setBackgroundTintList(null);
        android.graphics.drawable.StateListDrawable primary=new android.graphics.drawable.StateListDrawable();
        primary.addState(new int[]{-android.R.attr.state_enabled},rounded(0xff3c3e49,0xff3c3e49));
        primary.addState(new int[]{},rounded(0xffffa0aa,0xfff58a97));
        start.setBackground(new RippleDrawable(ColorStateList.valueOf(0x30ffffff),primary,null));
        text(hr,"Prototype. Accuracy is not independently verified.",15).setTextColor(0xffccd0df);
        LinearLayout connection=card(page,0xff20252f,0xff20252f);
        text(connection,"YOUR WATCH",15).setTextColor(0xff9ddbd1);
        status=text(connection,"Disconnected",20);status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        battery=text(connection,"Battery: not read",17);
        selected=text(connection,"No watch selected",16);selected.setTextColor(0xffccd0df);
        notice=text(connection,"Keep the app open during measurement. Sensor shutdown is not independently confirmed.",16);
        reconnect=button(connection,"Reconnect selected watch",v -> {
            if(watch.canReconnect(visible,scanning,controller.hasConnection()))confirmWatch(watch.target(),watch.label());
        });
        disconnect=button(connection,"Disconnect",v -> {watch.userDisconnected();event("User tapped Disconnect; deliberate local cleanup requested.");stopScan();controller.disconnect();});
        LinearLayout setup=card(page,0xff20252f,0xff20252f);
        text(setup,"CONNECTION SETUP",15).setTextColor(0xffaebfff);
        modelButton=button(setup,"Choose model: V8 or V1",v -> chooseModel());
        scan=button(setup,"Find my watch (10 seconds)",v -> beginScan());
        deviceRows=new LinearLayout(this);deviceRows.setOrientation(LinearLayout.VERTICAL);setup.addView(deviceRows);
        read=button(setup,"Read battery / firmware",v -> controller.readIdentity());
        Button toggle=button(page,"Show connection details and log",v -> {});
        details=card(page,0xff20252f,0xff20252f);details.setVisibility(View.GONE);
        toggle.setOnClickListener(v -> {
            boolean expand=details.getVisibility()!=View.VISIBLE;details.setVisibility(expand?View.VISIBLE:View.GONE);
            toggle.setText(expand?"Hide connection details and log":"Show connection details and log");
        });
        firmware=text(details,"Firmware: not read",16);
        text(details,"This screen only. Nothing is uploaded or saved to disk.",16);
        button(details,"Clear diagnostic log",v -> {events.clear();log.setText("");});
        log=text(details,"",14);log.setTextIsSelectable(true);
        state(false,false);
    }
    private void chooseModel() {
        if(controller.hasConnection() || scanning)return;
        new AlertDialog.Builder(this).setTitle("Select the physical device model")
            .setItems(new String[]{"V8 (screenless sample)","V1 / 2208A"},(dialog,index) -> {
                if(!visible || controller.hasConnection())return;
                watch.clear(display);
                chosenAdapter=index==0?new V8Adapter():new V1Adapter();
                controller=new BleController(this,chosenAdapter,this);
                modelButton.setText("Selected model: "+chosenAdapter.model());
                deviceRows.removeAllViews();selected.setText("Model selected: "+chosenAdapter.model()+". No device connected.");
                battery.setText("Battery: -");firmware.setText("Firmware: -");display.reset();renderReading();
                state(false,false);
            }).show();
    }
    private TextView text(LinearLayout parent,String value,int size) {
        TextView t=new TextView(this);t.setText(value);t.setTextSize(size);t.setTextColor(0xfff5f6fa);t.setPadding(0,dp(6),0,dp(6));parent.addView(t);return t;
    }
    private Button button(LinearLayout parent,String title,View.OnClickListener listener) {
        Button b=new Button(this);b.setText(title);b.setAllCaps(false);b.setTextSize(18);b.setTextColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled},new int[]{}},new int[]{0xffaeb3c3,0xfff5f6fa}));
        b.setBackgroundTintList(null);b.setBackground(new RippleDrawable(ColorStateList.valueOf(0x30ffffff),rounded(0xff343b4c,0xff343b4c),null));b.setMinHeight(dp(56));b.setPadding(dp(12),dp(10),dp(12),dp(10));b.setOnClickListener(listener);LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2);params.setMargins(0,dp(6),0,dp(6));parent.addView(b,params);return b;
    }
    private GradientDrawable rounded(int first,int last){
        GradientDrawable shape=new GradientDrawable(GradientDrawable.Orientation.TL_BR,new int[]{first,last});shape.setCornerRadius(dp(20));return shape;
    }
    private LinearLayout card(LinearLayout parent,int first,int last){
        LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(dp(18),dp(16),dp(18),dp(16));box.setBackground(rounded(first,last));
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2);params.setMargins(0,0,0,dp(16));parent.addView(box,params);return box;
    }
    private int dp(int value){return Math.round(value*getResources().getDisplayMetrics().density);}
    private void setIfChanged(TextView view,String text){if(!text.contentEquals(view.getText()))view.setText(text);}
    private void renderReading(){
        if(heart==null)return;
        setIfChanged(heart,display.bpm()==null?"-- bpm":display.bpm()+" bpm");
        setIfChanged(readingState,display.label(android.os.SystemClock.elapsedRealtime()));
        setIfChanged(received,display.bpm()==null?"No reading received":"Last received: "+timestamp.format(new Date(display.receivedWall())));
    }
    @Override protected void onStart() {super.onStart();visible=true;handler.removeCallbacks(refresh);refresh.run();updateReconnect();}
    private void applyScreenAwake(){
        if(awake.keepAwake())getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }
    @Override protected void onResume(){super.onResume();awake.foreground(true);applyScreenAwake();}
    @Override protected void onPause(){awake.foreground(false);applyScreenAwake();super.onPause();}
    @Override protected void onDestroy(){awake.destroy();applyScreenAwake();super.onDestroy();}
    @Override protected void onStop() {
        watch.leftForeground(controller.hasConnection());
        awake.foreground(false);applyScreenAwake();
        event("Foreground exit (Activity.onStop); deliberate local cleanup requested. No automatic reconnect.");
        visible=false;handler.removeCallbacks(refresh);display.session(false);renderReading();stopScan();controller.disconnect();super.onStop();
    }
    @Override public void event(String message) {
        if(log==null)return;
        events.addLast(timestamp.format(new Date())+"  "+message);
        while(events.size()>60)events.removeFirst();
        log.setText(String.join("\n",events));
        if(notice!=null && !(message.startsWith("RX") || message.startsWith("TX") || message.startsWith("HR candidate")
            || message.startsWith("Activity frame") || message.startsWith("Manual HR command") || message.startsWith("Unclassified"))) {
            if(message.contains("HR stop=true, reporting disable=true"))
                notice.setText("Stop commands delivered. Physical sensor shutdown is not independently confirmed.");
            else notice.setText(message);
        }
    }
    @Override public void state(boolean ready,boolean measuring) {
        awake.measurement(measuring);applyScreenAwake();
        connected=ready;measuringNow=measuring;display.session(measuring);renderReading();
        modelButton.setEnabled(!controller.hasConnection() && !scanning);
        status.setText(controller.hasConnection()?controller.statusText():watch.disconnectedText());
        updateReconnect();
        read.setEnabled(ready && !measuring);start.setEnabled(ready || measuring);
        start.setText(measuring?"Stop measurement":"Measure heart rate - 30 seconds");
        scan.setEnabled(!controller.hasConnection() && !scanning);
        disconnect.setEnabled(controller.hasConnection());
    }
    @Override public void reading(DeviceAdapter.Reading reading,String raw,long receivedAt) {
        String when=timestamp.format(new Date(receivedAt));
        switch(reading.kind) {
            case BATTERY: battery.setText("Battery (last read): "+reading.value+"%");break;
            case FIRMWARE: firmware.setText("Firmware: "+reading.text);break;
            case NO_RESULT: break; // Keep the last reading with its age; zero is not a new BPM.
            case HEART_RATE: display.accept(reading.value,receivedAt,android.os.SystemClock.elapsedRealtime());renderReading();break;
            default:break;
        }
        event("RX "+reading.kind+" at "+when+": "+raw);
    }
    private void updateReconnect(){
        if(reconnect!=null)reconnect.setEnabled(watch.canReconnect(visible,scanning,controller.hasConnection()));
    }
    private void confirmWatch(final BluetoothDevice device,final String label){
        if(!visible || scanning || controller.hasConnection() || chosenAdapter==null)return;
        final DeviceAdapter model=chosenAdapter;
        new AlertDialog.Builder(this).setTitle("Confirm physical model: "+model.model())
            .setMessage(label+"\n\nConnect only if this is your selected physical watch. This does not start a measurement or pair the device.")
            .setNegativeButton("Cancel",null).setPositiveButton("Connect "+model.model(),(dialog,which) -> {
                if(!visible || scanning || controller.hasConnection() || chosenAdapter!=model)return;
                if(!permissions()){event("Nearby devices permission is required. Tap Find my watch to review permission; no reconnect started.");return;}
                watch.select(device,label,display);
                BluetoothDevice target=watch.explicitRequest(visible,scanning,controller.hasConnection(),display);
                if(target==null)return;
                deviceRows.removeAllViews();selected.setText(label+"\nModel confirmed by you: "+model.model());
                battery.setText("Battery: not read");firmware.setText("Firmware: not read");renderReading();
                controller.connect(target);
            }).show();
    }
    private boolean permissions() {
        return checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN)==PackageManager.PERMISSION_GRANTED
            && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)==PackageManager.PERMISSION_GRANTED;
    }
    private void beginScan() {
        if(!visible || scanning || controller.hasConnection())return;
        if(chosenAdapter==null){event("Choose V8 or V1 before scanning.");return;}
        if(!permissions()) {
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_SCAN,Manifest.permission.BLUETOOTH_CONNECT},1);return;
        }
        BluetoothManager manager=getSystemService(BluetoothManager.class);
        BluetoothAdapter bluetooth=manager==null?null:manager.getAdapter();
        if(bluetooth==null) {event("This phone has no available BLE adapter.");return;}
        try {
            if(!bluetooth.isEnabled()) {event("Bluetooth is off. Enable it yourself if you want to run this test.");return;}
            scanner=bluetooth.getBluetoothLeScanner();
            if(scanner==null) {event("BLE scanner unavailable.");return;}
            devices.clear();deviceRows.removeAllViews();scanning=true;scanGeneration++;updateReconnect();scan.setEnabled(false);modelButton.setEnabled(false);
            scanCallback=createScanCallback(scanGeneration);
            scanner.startScan(null,new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),scanCallback);
            scanTimeout=this::stopScan;handler.postDelayed(scanTimeout,10000);
            event("Scanning for 10 seconds. Select only your known device for the chosen model.");
        } catch(SecurityException | IllegalStateException e) {stopScan();event("Scan unavailable. Check Bluetooth and Nearby devices permission.");}
    }
    private void stopScan() {
        if(scanTimeout!=null)handler.removeCallbacks(scanTimeout);
        if(scanning && scanner!=null)try{scanner.stopScan(scanCallback);}catch(SecurityException | IllegalStateException ignored){}
        scanning=false;scanGeneration++;if(scan!=null)scan.setEnabled(!controller.hasConnection());
        if(modelButton!=null)modelButton.setEnabled(!controller.hasConnection());
        updateReconnect();
    }
    private ScanCallback createScanCallback(final int generation) { return new ScanCallback() {
        @Override public void onScanResult(int type,ScanResult result) {
            handler.post(() -> {
                if(!scanning || !visible || generation!=scanGeneration)return;
                try {
                    BluetoothDevice d=result.getDevice();String address=d.getAddress();
                    if(devices.containsKey(address) || devices.size()>=30)return;
                    devices.put(address,d);
                    String name=result.getScanRecord()==null?null:result.getScanRecord().getDeviceName();
                    if(name==null)name=d.getName();if(name==null)name="Unnamed BLE device";
                    final String label=name+" · "+address;
                    button(deviceRows,label+" ("+result.getRssi()+" dBm)",v -> {
                        stopScan();
                        confirmWatch(d,label);
                    });
                }catch(SecurityException e){stopScan();event("Bluetooth permission unavailable.");}
            });
        }
        @Override public void onScanFailed(int error) {handler.post(() -> {
            if(!scanning || generation!=scanGeneration)return;
            stopScan();event("Scan failed: "+error);
        });}
    }; }
    @Override public void onRequestPermissionsResult(int code,String[] names,int[] results) {
        super.onRequestPermissionsResult(code,names,results);
        if(code==1)event(permissions()?"Permission granted. Tap Find my device to begin.":"Nearby devices permission is required. No scan started.");
    }
}
