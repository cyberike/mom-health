package org.momhealth.gateway;
import org.momhealth.gateway.data.BatchUploader;
import org.json.JSONObject;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;

/** Optional same-origin technical status upload; only under explicit upload consent. */
public final class GatewayStatusClient {
    public static final class LocalSnapshot {public long pending,uploaded;public String lastObservation;}
    /** Bounded by the store's 10,000-row capacity; does not attribute another watch's counts. */
    public static LocalSnapshot localSnapshot(org.momhealth.gateway.data.HealthStore store,String deviceId){
        LocalSnapshot result=new LocalSnapshot();long before=0;
        for(int page=0;page<100;page++){java.util.List<org.momhealth.gateway.data.HealthStore.HistoryEntry> rows=store.history(100,before);for(org.momhealth.gateway.data.HealthStore.HistoryEntry row:rows)if(deviceId.equals(row.sample.deviceId)){if(result.lastObservation==null)result.lastObservation=row.sample.receivedAt;if(row.uploaded)result.uploaded++;else result.pending++;}if(rows.size()<100)break;before=rows.get(rows.size()-1).rowId;}
        return result;
    }
    private final URL endpoint;private final String token;private volatile boolean cancelled;private volatile HttpURLConnection active;
    public GatewayStatusClient(String batchEndpoint,String token){new BatchUploader(batchEndpoint,false,token);this.token=token;try{URI batch=new URI(batchEndpoint);endpoint=new URI(batch.getScheme(),null,batch.getHost(),batch.getPort(),"/v1/gateway-status",null,null).toURL();}catch(Exception ex){throw new IllegalArgumentException("Invalid status destination",ex);}}
    public void cancel(){cancelled=true;HttpURLConnection c=active;if(c!=null)c.disconnect();}
    private void check()throws IOException{if(cancelled||Thread.currentThread().isInterrupted())throw new IOException("Cancelled");}
    public void send(String deviceId,String model,String lastObservation,Integer battery,String connection,long pending,long uploaded,boolean recording)throws Exception{
        check();JSONObject body=new JSONObject();body.put("schema_version",1);body.put("device_id",deviceId);body.put("model",model);body.put("reported_at",Instant.now().toString());body.put("last_observation_at",lastObservation==null?JSONObject.NULL:lastObservation);body.put("battery_percent",battery==null?JSONObject.NULL:battery);body.put("connection",connection);body.put("pending_samples",pending);body.put("uploaded_samples",uploaded);body.put("recording_enabled",recording);body.put("synthetic",false);
        byte[] bytes=body.toString().getBytes(StandardCharsets.UTF_8);if(bytes.length>16384)throw new IOException("Status payload too large");HttpURLConnection c=(HttpURLConnection)endpoint.openConnection();active=c;
        try{check();c.setConnectTimeout(5000);c.setReadTimeout(5000);c.setInstanceFollowRedirects(false);c.setUseCaches(false);c.setRequestMethod("POST");c.setDoOutput(true);c.setFixedLengthStreamingMode(bytes.length);c.setRequestProperty("Content-Type","application/json; charset=utf-8");if(token!=null)c.setRequestProperty("Authorization","Bearer "+token);
            try(OutputStream out=c.getOutputStream()){check();out.write(bytes);}check();if(c.getResponseCode()!=200)throw new IOException("Status was not accepted");ByteArrayOutputStream out=new ByteArrayOutputStream();try(InputStream in=c.getInputStream()){byte[] buffer=new byte[1024];int n;while((n=in.read(buffer))!=-1){check();if(out.size()+n>16384)throw new IOException("Status response too large");out.write(buffer,0,n);}}
            JSONObject response=new JSONObject(new String(out.toByteArray(),StandardCharsets.UTF_8));if(!Boolean.TRUE.equals(response.get("accepted")))throw new IOException("Invalid status acknowledgment");check();
        }finally{c.disconnect();active=null;}
    }
}
