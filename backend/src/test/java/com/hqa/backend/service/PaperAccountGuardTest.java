package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.UserRepository;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

class PaperAccountGuardTest {
    @Test
    void fingerprintIsStableAcrossCiphertextAndUserButOwnershipCannotBeShared() throws Exception {
        HqaProperties properties = new HqaProperties();
        properties.setKisEncKey("local-test-key-not-a-credential");
        SecretCipher cipher = new SecretCipher(properties);
        cipher.init();
        UserRepository users = mock(UserRepository.class);
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        PaperAccountGuard guard = new PaperAccountGuard(users, cipher, jdbc);
        User first = PaperTradeStoreTest.user();
        first.getSecret().setKisAccountNo(cipher.encrypt("12345678"));
        User second = PaperTradeStoreTest.user();
        second.setUserId("u2");
        second.getSecret().setKisAccountNo(cipher.encrypt("12345678"));
        assertThat(first.getSecret().getKisAccountNo()).isNotEqualTo(second.getSecret().getKisAccountNo());
        assertThat(guard.binding(first)).isEqualTo(guard.binding(second)).doesNotContain("12345678");
        when(users.lockByUserId("u2")).thenReturn(Optional.of(second));
        when(jdbc.queryForObject(anyString(), eq(String.class), any(Object[].class))).thenReturn("u1");
        assertThatThrownBy(() -> guard.lock("u2")).hasMessage("PAPER_ACCOUNT_ALREADY_BOUND_TO_ANOTHER_USER");
        second.getSecret().setKisIsReal(true);
        assertThatThrownBy(() -> guard.binding(second)).hasMessage("PAPER_ACCOUNT_REQUIRED");
    }
}
