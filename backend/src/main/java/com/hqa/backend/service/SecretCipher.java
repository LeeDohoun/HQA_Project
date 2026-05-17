package com.hqa.backend.service;

import com.hqa.backend.config.HqaProperties;
import jakarta.annotation.PostConstruct;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.stereotype.Component;

/**
 * AES-GCM symmetric encryption for KIS credentials stored in the database.
 *
 * Ciphertext format (Base64 of):  [12-byte IV][ciphertext+16-byte GCM tag]
 *
 * The master key is read from HQA_KIS_ENC_KEY (env) / hqa.kis-enc-key (yaml)
 * and SHA-256 hashed to a 256-bit AES key.
 */
@Component
public class SecretCipher {

    private static final String ALGO = "AES/GCM/NoPadding";
    private static final int IV_BYTES = 12;
    private static final int TAG_BITS = 128;
    private static final String PREFIX = "enc:v1:";

    private final HqaProperties properties;
    private final SecureRandom random = new SecureRandom();
    private SecretKeySpec keySpec;

    public SecretCipher(HqaProperties properties) {
        this.properties = properties;
    }

    @PostConstruct
    void init() throws Exception {
        String raw = properties.getKisEncKey();
        if (raw == null || raw.isBlank()) {
            throw new IllegalStateException(
                    "hqa.kis-enc-key (env HQA_KIS_ENC_KEY) is required to encrypt KIS credentials");
        }
        byte[] key = MessageDigest.getInstance("SHA-256").digest(raw.getBytes(StandardCharsets.UTF_8));
        this.keySpec = new SecretKeySpec(key, "AES");
    }

    public String encrypt(String plaintext) {
        if (plaintext == null) {
            return null;
        }
        try {
            byte[] iv = new byte[IV_BYTES];
            random.nextBytes(iv);
            Cipher cipher = Cipher.getInstance(ALGO);
            cipher.init(Cipher.ENCRYPT_MODE, keySpec, new GCMParameterSpec(TAG_BITS, iv));
            byte[] ct = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));
            byte[] out = new byte[iv.length + ct.length];
            System.arraycopy(iv, 0, out, 0, iv.length);
            System.arraycopy(ct, 0, out, iv.length, ct.length);
            return PREFIX + Base64.getEncoder().encodeToString(out);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to encrypt KIS credential", e);
        }
    }

    public String decrypt(String ciphertext) {
        if (ciphertext == null) {
            return null;
        }
        // Legacy plaintext rows written before encryption was enabled.
        if (!ciphertext.startsWith(PREFIX)) {
            return ciphertext;
        }
        try {
            byte[] blob = Base64.getDecoder().decode(ciphertext.substring(PREFIX.length()));
            byte[] iv = new byte[IV_BYTES];
            byte[] ct = new byte[blob.length - IV_BYTES];
            System.arraycopy(blob, 0, iv, 0, IV_BYTES);
            System.arraycopy(blob, IV_BYTES, ct, 0, ct.length);
            Cipher cipher = Cipher.getInstance(ALGO);
            cipher.init(Cipher.DECRYPT_MODE, keySpec, new GCMParameterSpec(TAG_BITS, iv));
            return new String(cipher.doFinal(ct), StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to decrypt KIS credential", e);
        }
    }
}
