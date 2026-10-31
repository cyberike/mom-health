package org.momhealth.gateway.data;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CancellationException;
import org.json.JSONException;

/** Single explicit synchronous request. No redirect, credential, retry or scheduling support. */
public final class BatchUploader {
    private final URL endpoint;
    private final String bearerToken;
    private volatile boolean cancelled;
    private volatile HttpURLConnection active;
    public BatchUploader(String endpoint,boolean allowLoopbackHttp) {
        this(endpoint,allowLoopbackHttp,null);
    }
    /** Optional user-supplied credential remains in this instance only; never logged or stored. */
    public BatchUploader(String endpoint,boolean allowLoopbackHttp,String bearerToken) {
        try {
            URI uri=new URI(endpoint); String host=uri.getHost();
            boolean loopback="localhost".equalsIgnoreCase(host)||"127.0.0.1".equals(host)||"[::1]".equals(host)||"::1".equals(host);
            if(host==null || uri.getRawUserInfo()!=null || uri.getRawQuery()!=null || uri.getRawFragment()!=null || !"/v1/batches".equals(uri.getPath())) throw new IllegalArgumentException("Use a /v1/batches URL without credentials or query");
            if(!"https".equalsIgnoreCase(uri.getScheme()) && !(allowLoopbackHttp&&loopback&&"http".equalsIgnoreCase(uri.getScheme()))) throw new IllegalArgumentException("HTTPS required except explicitly allowed loopback HTTP");
            this.endpoint=uri.toURL();
            if(bearerToken!=null && (bearerToken.length()<32 || bearerToken.length()>4096 || !bearerToken.matches("[A-Za-z0-9._~+/=-]+") || !"https".equalsIgnoreCase(uri.getScheme()))) throw new IllegalArgumentException("Bearer token requires HTTPS and 32..4096 valid token characters");
            this.bearerToken=bearerToken;
        } catch(URISyntaxException|MalformedURLException ex) { throw new IllegalArgumentException("Invalid endpoint",ex); }
    }
    public void cancel() { HttpURLConnection connection; synchronized(this) { cancelled=true; connection=active; } if(connection!=null) connection.disconnect(); }
    synchronized void acknowledgeIfActive(HealthStore store,UploadBatch batch,BatchAck ack) {
        checkCancelled(); store.acknowledge(batch,ack);
    }
    public boolean isCancelled() { return cancelled || Thread.currentThread().isInterrupted(); }
    private void checkCancelled() { if(isCancelled()) throw new CancellationException("Upload cancelled; samples remain pending"); }
    public BatchAck upload(UploadBatch batch) throws IOException {
        if(batch.samples.isEmpty()) throw new IllegalArgumentException("Empty batch");
        checkCancelled(); byte[] bytes=batch.toJson().getBytes(StandardCharsets.UTF_8);
        if(bytes.length>131072) throw new IOException("Batch exceeds request limit");
        HttpURLConnection connection=(HttpURLConnection)endpoint.openConnection(); active=connection;
        try {
            checkCancelled(); connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(10000); connection.setReadTimeout(10000);
            connection.setRequestMethod("POST"); connection.setDoOutput(true); connection.setUseCaches(false);
            connection.setRequestProperty("Content-Type","application/json; charset=utf-8"); connection.setRequestProperty("Accept","application/json"); connection.setFixedLengthStreamingMode(bytes.length);
            if(bearerToken!=null) connection.setRequestProperty("Authorization","Bearer "+bearerToken);
            try(OutputStream out=connection.getOutputStream()) { checkCancelled(); out.write(bytes); }
            checkCancelled(); int status=connection.getResponseCode(); if(status<200||status>=300) throw new IOException("Server returned HTTP "+status+"; samples remain pending");
            ByteArrayOutputStream output=new ByteArrayOutputStream();
            try(InputStream in=connection.getInputStream()) { byte[] chunk=new byte[4096];int n;
                while((n=in.read(chunk))!=-1) { checkCancelled(); if(output.size()+n>262144) throw new IOException("Response exceeds limit"); output.write(chunk,0,n); }
            }
            checkCancelled();
            try { return BatchAck.parse(batch,new String(output.toByteArray(),StandardCharsets.UTF_8)); }
            catch(JSONException ex) { throw new IOException("Invalid acknowledgment; samples remain pending",ex); }
        } finally { connection.disconnect(); active=null; }
    }
}
