package org.momhealth.gateway;
import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.momhealth.gateway.data.BatchUploader;

/** Key generation happens only on explicit runtime opt-in, never during a build. */
public final class CaregiverCredentialVault {
    private static final String ALIAS="mom-health-candidate-upload-v1";
    private final SharedPreferences prefs;
    public CaregiverCredentialVault(Context c){prefs=c.getSharedPreferences("caregiver-upload-vault",Context.MODE_PRIVATE);}
    private byte[] binding(String endpoint,String deviceId){return (endpoint+"\n"+deviceId).getBytes(StandardCharsets.UTF_8);}
    public void save(String endpoint,String deviceId,String token)throws Exception{
        new BatchUploader(endpoint,false,token);if(token==null)throw new IllegalArgumentException("Token required");
        KeyStore keys=KeyStore.getInstance("AndroidKeyStore");keys.load(null);
        if(!keys.containsAlias(ALIAS)){KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");generator.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setRandomizedEncryptionRequired(true).setUserAuthenticationRequired(false).build());generator.generateKey();}
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,(SecretKey)keys.getKey(ALIAS,null));cipher.updateAAD(binding(endpoint,deviceId));byte[] sealed=cipher.doFinal(token.getBytes(StandardCharsets.UTF_8));
        if(!prefs.edit().putString("iv",Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP)).putString("sealed",Base64.encodeToString(sealed,Base64.NO_WRAP)).commit())throw new IllegalStateException("Credential could not be saved");
    }
    public String read(String endpoint,String deviceId)throws Exception{
        String iv=prefs.getString("iv",null),sealed=prefs.getString("sealed",null);if(iv==null||sealed==null)return null;
        if(iv.length()>100||sealed.length()>6000)throw new IllegalStateException("Credential record invalid");KeyStore keys=KeyStore.getInstance("AndroidKeyStore");keys.load(null);if(!keys.containsAlias(ALIAS))throw new IllegalStateException("Credential key unavailable");
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,(SecretKey)keys.getKey(ALIAS,null),new GCMParameterSpec(128,Base64.decode(iv,Base64.NO_WRAP)));cipher.updateAAD(binding(endpoint,deviceId));
        String token=new String(cipher.doFinal(Base64.decode(sealed,Base64.NO_WRAP)),StandardCharsets.UTF_8);new BatchUploader(endpoint,false,token);return token;
    }
    /** Forgetting here does not revoke the credential on the server. */
    public boolean forget(){return prefs.edit().clear().commit();}
}
