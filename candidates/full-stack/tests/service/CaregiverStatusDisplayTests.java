package org.momhealth.gateway;

/** Portable presentation checks; no Android, device, storage or network access. */
public final class CaregiverStatusDisplayTests {
    private static int n;
    static void check(boolean ok){n++;if(!ok)throw new AssertionError("check "+n);}
    public static void main(String[] args){
        check(CaregiverStatusDisplay.budget(0,false,false).isEmpty());
        check(CaregiverStatusDisplay.budget(60001,false,false).contains("2 minutes"));
        check(CaregiverStatusDisplay.budget(1,false,false).contains("1 minute ("));
        check(CaregiverStatusDisplay.budget(60000,true,false).isEmpty());
        check(CaregiverStatusDisplay.budget(60000,false,true).isEmpty());
        check(CaregiverStatusDisplay.reading(null,null).contains("No automatic reading"));
        String value=CaregiverStatusDisplay.reading(74,"synthetic time");
        check(value.contains("74 bpm"));check(value.contains("not a live measurement"));check(value.contains("accuracy unverified"));
        check(CaregiverStatusDisplay.upload(false,false,false).equals("Automatic upload is off."));
        check(CaregiverStatusDisplay.upload(true,false,false).contains("credential unavailable"));
        check(CaregiverStatusDisplay.upload(true,true,true).contains("paused after reboot"));
        check(CaregiverStatusDisplay.upload(true,true,false).equals("Automatic upload is enabled."));
        System.out.println(n+" caregiver status checks passed (synthetic; no Android runtime).");
    }
}
