package org.momhealth.gateway;
import java.util.Locale;
import java.util.UUID;

/** Local caregiver selection, never a measurement or authorization to connect. */
public final class RememberedWatch {
    public final String model,address,label,deviceId;
    public RememberedWatch(String model,String address,String label,String deviceId){
        if(!"V1".equals(model)&&!"V8".equals(model))throw new IllegalArgumentException("Unsupported model");
        if(address==null||!address.matches("(?i)[0-9a-f]{2}(:[0-9a-f]{2}){5}"))throw new IllegalArgumentException("Invalid local address");
        if(label==null||label.isEmpty()||label.length()>256)throw new IllegalArgumentException("Invalid label");
        if(deviceId==null||!deviceId.matches("[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"))throw new IllegalArgumentException("Invalid opaque ID");
        this.model=model;this.address=address.toUpperCase(Locale.ROOT);this.label=label;this.deviceId=UUID.fromString(deviceId).toString();
    }
    public boolean matches(String candidateModel,String candidateAddress){return model.equals(candidateModel)&&address.equalsIgnoreCase(candidateAddress);}
}
