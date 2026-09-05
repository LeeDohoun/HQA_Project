package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.UserRepository;
import org.junit.jupiter.api.Test;

class AutoTradeServiceTest {
    @Test
    void onlyPaperCanBeEnabledButAnyAccountCanBeDisabled() {
        UserRepository users = mock(UserRepository.class);
        AutoTradeService service = new AutoTradeService(users);
        User user = PaperTradeStoreTest.user();
        user.getSecret().setKisIsReal(true);
        assertThatThrownBy(() -> service.setEnabled(user, true)).hasMessage("PAPER_ACCOUNT_REQUIRED");
        verifyNoInteractions(users);
        assertThat(service.setEnabled(user, false)).isFalse();
        user.getSecret().setKisIsReal(false);
        assertThat(service.setEnabled(user, true)).isTrue();
        verify(users, times(2)).save(user);
    }
}
