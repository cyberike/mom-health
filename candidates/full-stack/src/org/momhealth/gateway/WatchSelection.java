package org.momhealth.gateway;

/** In-memory, activity-lifetime selection. Only explicit UI requests return a target. */
public final class WatchSelection<T> {
    public enum Exit { NONE, FOREGROUND, USER }
    private T target;
    private String label;
    private Exit exit=Exit.NONE;
    public T target(){return target;}
    public String label(){return label;}
    public void clear(ReadingDisplay display){target=null;label=null;exit=Exit.NONE;display.reset();}
    public void select(T value,String name,ReadingDisplay display){target=value;label=name;exit=Exit.NONE;display.reset();}
    public boolean canReconnect(boolean foreground,boolean scanning,boolean connectionExists){return target!=null && foreground && !scanning && !connectionExists;}
    public T explicitRequest(boolean foreground,boolean scanning,boolean connectionExists,ReadingDisplay display){
        if(!canReconnect(foreground,scanning,connectionExists))return null;
        exit=Exit.NONE;display.reset();return target;
    }
    public void leftForeground(boolean connectionExists){if(connectionExists)exit=Exit.FOREGROUND;}
    public void userDisconnected(){exit=Exit.USER;}
    public String disconnectedText(){
        if(target==null)return "Disconnected - choose a model and watch";
        if(exit==Exit.FOREGROUND)return "Disconnected after leaving the app or screen sleep. Tap Reconnect selected watch.";
        if(exit==Exit.USER)return "Disconnected by you. Tap Reconnect selected watch when ready.";
        return "Disconnected. Tap Reconnect selected watch, or Find my watch if it cannot connect.";
    }
}
