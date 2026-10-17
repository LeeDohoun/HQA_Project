package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.repository.TradePlanReceiptRepository;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import com.hqa.backend.repository.UserRepository;
import com.hqa.backend.repository.WatchlistItemRepository;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Strategy accounts are not LLM auto-trade targets and never store LLM v1/v2 plans. */
class StrategyLlmExclusionTest {
    private final StrategyAccountRepository strategyAccounts = mock(StrategyAccountRepository.class);

    @Test
    void activeTargetsExcludeStrategyAccounts() {
        UserRepository users = mock(UserRepository.class);
        WatchlistItemRepository watchlist = mock(WatchlistItemRepository.class);
        User llm = user("llm");
        User strategy = user("strategy");
        when(users.findAllActiveWithSecretAndPreference()).thenReturn(List.of(llm, strategy));
        when(watchlist.findByUserOrderByCreatedAtDesc(any())).thenReturn(List.of());
        when(strategyAccounts.findAll()).thenReturn(List.of(
                new StrategyAccount("strategy", "hf001_f_both", "observe", false, OffsetDateTime.now())));
        AutoTradeTargetService service = new AutoTradeTargetService(users, watchlist, strategyAccounts);
        List<?> targets = (List<?>) service.activeTargets().get("targets");
        assertThat(targets).hasSize(1);
        assertThat(((Map<?, ?>) targets.get(0)).get("userId")).isEqualTo("llm");
    }

    @Test
    void llmPlansAreRejectedForStrategyAccountsBeforeAnyAccountLock() {
        PaperAccountGuard guard = mock(PaperAccountGuard.class);
        TradeSignalRepository signals = mock(TradeSignalRepository.class);
        PaperTradeStore store = new PaperTradeStore(signals, mock(TradeSignalExecutionRepository.class), guard,
                new ObjectMapper(), mock(TradePlanReceiptRepository.class), strategyAccounts, 1, 0.5);
        when(strategyAccounts.existsById("u1")).thenReturn(true);
        InternalTradeSignalRequest request = mock(InternalTradeSignalRequest.class);
        when(request.userId()).thenReturn("u1");
        assertThatThrownBy(() -> store.save(request, Map.of(), OffsetDateTime.now()))
                .isInstanceOf(IllegalStateException.class).hasMessage("STRATEGY_ACCOUNT_REJECTS_LLM_PLANS");
        verifyNoInteractions(guard, signals);
    }

    @Test
    void theStrategyCheckIsRepeatedUnderTheAccountLock() {
        PaperAccountGuard guard = mock(PaperAccountGuard.class);
        PaperTradeStore store = new PaperTradeStore(mock(TradeSignalRepository.class), mock(TradeSignalExecutionRepository.class),
                guard, new ObjectMapper(), mock(TradePlanReceiptRepository.class), strategyAccounts, 1, 0.5);
        // The binding commits while save waits for the lock.
        when(strategyAccounts.existsById("u1")).thenReturn(false, true);
        when(guard.lock("u1")).thenReturn(PaperTradeStoreTest.user());
        InternalTradeSignalRequest request = mock(InternalTradeSignalRequest.class);
        when(request.userId()).thenReturn("u1");
        assertThatThrownBy(() -> store.save(request, Map.of(), OffsetDateTime.now()))
                .isInstanceOf(IllegalStateException.class).hasMessage("STRATEGY_ACCOUNT_REJECTS_LLM_PLANS");
        verify(guard).lock("u1");
    }

    private static User user(String userId) {
        User user = new User();
        user.setUserId(userId);
        user.setAutoTradeEnabled(true);
        return user;
    }
}
