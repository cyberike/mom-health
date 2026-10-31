package org.momhealth.gateway;

/** Window presentation policy; never a wake lock or a measurement controller. */
public final class ScreenAwakeState {
    private boolean foreground,collecting,destroyed;
    public void foreground(boolean value){foreground=value;}
    public void measurement(boolean value){collecting=value;}
    public void destroy(){destroyed=true;foreground=false;collecting=false;}
    public boolean keepAwake(){return foreground && collecting && !destroyed;}
}
