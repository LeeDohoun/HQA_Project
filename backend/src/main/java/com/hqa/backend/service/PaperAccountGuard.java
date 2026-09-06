package com.hqa.backend.service;

import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.UserRepository;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PaperAccountGuard {
    private final UserRepository users;
    private final SecretCipher cipher;
    private final JdbcTemplate jdbc;

    public PaperAccountGuard(UserRepository users, SecretCipher cipher, JdbcTemplate jdbc) {
        this.users = users;
        this.cipher = cipher;
        this.jdbc = jdbc;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public User lock(String userId) {
        User user = users.lockByUserId(userId).orElseThrow(() -> new IllegalArgumentException("USER_NOT_FOUND"));
        String binding = binding(user);
        String appKey = cipher.decrypt(user.getSecret().getKisAppKey());
        if (appKey == null || appKey.isBlank()) throw new IllegalStateException("KIS_SECRET_MISSING");
        String credential = cipher.fingerprint("PAPER_APP_KEY:" + appKey);
        jdbc.update("INSERT INTO public.paper_broker_accounts(account_binding, credential_binding, user_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                binding, credential, userId);
        String owner;
        try {
            owner = jdbc.queryForObject("SELECT user_id FROM public.paper_broker_accounts WHERE account_binding = ? FOR UPDATE",
                    String.class, binding);
        } catch (org.springframework.dao.EmptyResultDataAccessException ex) {
            throw new IllegalStateException("PAPER_CREDENTIAL_ALREADY_BOUND_TO_ANOTHER_ACCOUNT", ex);
        }
        if (!userId.equals(owner)) throw new IllegalStateException("PAPER_ACCOUNT_ALREADY_BOUND_TO_ANOTHER_USER");
        jdbc.update("UPDATE public.paper_broker_accounts SET credential_binding = ? WHERE account_binding = ?", credential, binding);
        return user;
    }

    public String binding(User user) {
        UserSecret secret = user.getSecret();
        if (secret == null || secret.isKisIsReal()) throw new IllegalStateException("PAPER_ACCOUNT_REQUIRED");
        String account = cipher.decrypt(secret.getKisAccountNo());
        String product = secret.getKisAccountProductCode();
        if (account == null || !account.matches("[0-9]{8}") || product == null || !product.matches("[0-9]{2}")) {
            throw new IllegalStateException("PAPER_ACCOUNT_IDENTITY_INVALID");
        }
        return cipher.fingerprint("PAPER:" + account + ":" + product);
    }
}
