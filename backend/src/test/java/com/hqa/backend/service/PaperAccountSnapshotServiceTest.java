package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.hqa.backend.entity.PaperAccountBaseline;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.*;
import java.time.Clock;
import java.time.OffsetDateTime;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class PaperAccountSnapshotServiceTest {
    private final UserRepository users = mock(UserRepository.class);
    private final KisClient kis = mock(KisClient.class);
    private final PaperAccountBaselineRepository baselines = mock(PaperAccountBaselineRepository.class);
    private final TradeSignalExecutionRepository executions = mock(TradeSignalExecutionRepository.class);
    private final TradeSignalRepository signals = mock(TradeSignalRepository.class);
    private final PaperTradeStore store = mock(PaperTradeStore.class);
    private final PaperAccountGuard accountGuard = mock(PaperAccountGuard.class);
    private final org.springframework.transaction.PlatformTransactionManager transactionManager = mock(org.springframework.transaction.PlatformTransactionManager.class);
    private final User user = PaperTradeStoreTest.user();
    private final OffsetDateTime now = OffsetDateTime.parse("2026-09-04T10:00:00+09:00");
    private final PaperAccountSnapshotService service = new PaperAccountSnapshotService(users, kis, baselines,
            executions, signals, store, accountGuard, transactionManager, Clock.fixed(now.toInstant(), now.getOffset()));

    @BeforeEach
    void setup() {
        when(users.lockByUserId("u1")).thenReturn(Optional.of(user));
        when(users.findByUserId("u1")).thenReturn(Optional.of(user));
        when(kis.fetchAccessToken("u1", user.getSecret())).thenReturn("token");
        when(baselines.save(any())).thenAnswer(inv -> inv.getArgument(0));
        when(store.monitorCapacity()).thenReturn(10);
        when(accountGuard.binding(user)).thenReturn("binding");
    }

    @Test
    void missingMiddayBaselineDisablesEntryWithoutInventingZeroPnl() {
        when(kis.inquireBalance(anyString(), any(), anyString())).thenReturn(balance(null));
        Map<String, Object> snapshot = service.snapshot("u1");
        assertThat(snapshot).containsEntry("success", true).containsEntry("entryEligible", false)
                .containsEntry("dailyPnlPct", null).containsEntry("entryBlockReason", "DAILY_BASELINE_UNAVAILABLE")
                .containsEntry("maxPositionPct", 20.0);
        verify(baselines, never()).save(any());
    }

    @Test
    void actualPriorDayEquityProducesLossAndKeepsHoldingsAvailable() {
        when(kis.inquireBalance(anyString(), any(), anyString())).thenReturn(balance(10000L));
        Map<String, Object> snapshot = service.snapshot("u1");
        assertThat(snapshot).containsEntry("success", true).containsEntry("dailyPnlPct", -6.0)
                .containsEntry("entryEligible", false).containsEntry("entryBlockReason", "DAILY_LOSS_LIMIT_EXCEEDED")
                .containsEntry("dailyPnlBaselineSource", "kis_previous_day_assets");
        assertThat((List<?>) snapshot.get("holdings")).hasSize(1);
    }

    @Test
    void persistedBaselineIsNotResetByMiddayBalance() {
        when(baselines.findById(anyString())).thenReturn(Optional.of(
                new PaperAccountBaseline("u1", now.toLocalDate(), 10000, now, "kis_preopen_equity")));
        when(kis.inquireBalance(anyString(), any(), anyString())).thenReturn(balance(9400L));
        assertThat(service.snapshot("u1")).containsEntry("dailyPnlPct", -6.0);
        verify(baselines, never()).save(any());
    }

    @Test
    void realCredentialsAndMalformedBalancesFailExplicitly() {
        user.getSecret().setKisIsReal(true);
        assertThat(service.snapshot("u1")).containsEntry("success", false).containsEntry("error", "PAPER_ACCOUNT_REQUIRED");
        verifyNoInteractions(kis);
        user.getSecret().setKisIsReal(false);
        when(kis.inquireBalance(anyString(), any(), anyString())).thenReturn(Map.of("success", true));
        assertThat(service.snapshot("u1")).containsEntry("success", false).doesNotContainKeys("orderableCash", "equity");
    }

    @Test
    void accountFailureIsCaughtOutsideTransactionCommitAndDoesNotPoisonNextAccount() {
        when(kis.inquireBalance(anyString(), any(), anyString())).thenReturn(balance(10000L));
        doThrow(new org.springframework.transaction.UnexpectedRollbackException("rollback-only"))
                .doNothing().when(transactionManager).commit(any());
        assertThat(service.snapshot("u1")).containsEntry("success", false).containsEntry("error", "rollback-only");
        assertThat(service.snapshot("u1")).containsEntry("success", true);
    }

    private Map<String, Object> balance(Long previous) {
        Map<String, Object> summary = new HashMap<>(Map.of("netAssetAmount", 9400L, "deposit", 9000L));
        if (previous != null) summary.put("previousDayTotalAssets", previous);
        return Map.of("success", true, "summary", summary, "holdings", List.of(Map.of(
                "stockCode", "005930", "stockName", "Samsung", "quantity", 4, "sellableQuantity", 3,
                "avgPrice", 110.0, "currentPrice", 100L, "evalAmount", 400L, "evalProfitRate", -9.1)));
    }
}
