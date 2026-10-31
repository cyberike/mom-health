package org.momhealth.gateway;
import android.content.Context;
import android.content.SharedPreferences;

/** App-private local setup only. Backup is disabled in the candidate manifest. */
final class CaregiverSetupStore {
    private final SharedPreferences prefs;
    CaregiverSetupStore(Context context){prefs=context.getSharedPreferences("caregiver-watch-v1",Context.MODE_PRIVATE);}
    RememberedWatch read(){
        if(!prefs.contains("version"))return null;
        if(prefs.getInt("version",0)!=1)throw new IllegalStateException("Unsupported saved setup version");
        return new RememberedWatch(prefs.getString("model",null),prefs.getString("address",null),prefs.getString("label",null),prefs.getString("deviceId",null));
    }
    boolean save(RememberedWatch watch){return prefs.edit().putInt("version",1).putString("model",watch.model).putString("address",watch.address).putString("label",watch.label).putString("deviceId",watch.deviceId).commit();}
    boolean forget(){return prefs.edit().clear().commit();}
}
