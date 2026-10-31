package org.momhealth.gateway;
import android.content.*;
import android.os.UserManager;

/** Unlocked-boot recovery only after separately persisted caregiver opt-in. */
public final class CaregiverBootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent){
        if(intent==null||!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()))return;
        CaregiverAutomationStore store=new CaregiverAutomationStore(context);
        try{CaregiverAutomationStore.Settings settings=store.read();if(settings==null||!settings.restore||!store.enabled()||!context.getSystemService(UserManager.class).isUserUnlocked()||!CaregiverService.prerequisites(context))return;
            // Android 15 forbids boot-launched dataSync FGS. Only connectedDevice restoration here.
            if(!store.pauseBootUpload(true)){store.status("Restart marker could not be stored; no operation started.");return;}CaregiverUploadJobService.schedule(context,settings);if(!CaregiverService.reserveStart())return;
            try{context.startForegroundService(new Intent(context,CaregiverService.class).setAction(CaregiverService.RESTORE));}catch(RuntimeException ex){CaregiverService.releaseReservation();store.status("Android declined automatic restart. Caregiver must review and start from the visible setup screen.");}
        }catch(RuntimeException ex){store.status("Saved restart configuration is invalid. No automatic work started.");}
    }
}
