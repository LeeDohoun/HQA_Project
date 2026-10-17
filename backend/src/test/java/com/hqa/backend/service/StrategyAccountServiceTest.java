package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.repository.StrategyBatchRepository;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class StrategyAccountServiceTest {
    private final StrategyTestFixture f = new StrategyTestFixture();
    private final TradeSignalRepository signals = mock(TradeSignalRepository.class);
    private final TradeSignalExecutionRepository executions = mock(TradeSignalExecutionRepository.class);
    private final StrategyBatchRepository batches = mock(StrategyBatchRepository.class);
    private final StrategyAccountService service = new StrategyAccountService(f.accounts, f.snapshots, signals, executions,
            batches, f.orders, f.guard, f.peaks, f.sessions);

    StrategyAccountServiceTest() {
        f.accountRows.clear();
        when(f.accounts.save(any())).thenAnswer(inv -> {
            StrategyAccount account = inv.getArgument(0);
            f.accountRows.put(account.getUserId(), account);
            return account;
        });
        when(f.accounts.findByStrategyId(anyString())).thenAnswer(inv -> f.accountRows.values().stream()
                .filter(account -> account.getStrategyId().equals(inv.getArgument(0))).findFirst());
    }

    @Test
    void bindsAPaperUserAndUpdatesItsStage() {
        Map<String, Object> view = service.bind("u1", "hf001_f_both", "observe", null);
        assertThat(view).containsEntry("userId", "u1").containsEntry("strategyId", "hf001_f_both")
                .containsEntry("stage", "observe").containsEntry("enabled", false);
        verify(f.snapshots).paperUser("u1");
        verify(f.guard).lock("u1");
        assertThat(view).containsEntry("equityPeakReset", true);
        assertThat(service.bind("u1", "hf001_f_both", "paper", true)).containsEntry("equityPeakReset", false);
        assertThat(f.accountRows.get("u1").getStage()).isEqualTo("paper");
        assertThat(f.accountRows.get("u1").isEnabled()).isTrue();
        assertThat(service.isStrategyAccount("u1")).isTrue();
        assertThat(service.isStrategyAccount("u2")).isFalse();
    }

    @Test
    void oneStrategyPerUserAndOneUserPerStrategy() {
        service.bind("u1", "hf001_f_both", "observe", false);
        assertThatThrownBy(() -> service.bind("u1", "av001_av_all", "observe", false))
                .isInstanceOf(IllegalStateException.class).hasMessage("USER_ALREADY_BOUND_TO_ANOTHER_STRATEGY");
        when(f.snapshots.paperUser("u2")).thenReturn(PaperTradeStoreTest.user());
        assertThatThrownBy(() -> service.bind("u2", "hf001_f_both", "observe", false))
                .isInstanceOf(IllegalStateException.class).hasMessage("STRATEGY_ALREADY_BOUND_TO_ANOTHER_USER");
    }

    @Test
    void realAccountsInvalidInputAndLlmPlansAreRefused() {
        when(f.snapshots.paperUser("real")).thenThrow(new IllegalArgumentException("PAPER_ACCOUNT_REQUIRED"));
        assertThatThrownBy(() -> service.bind("real", "hf001_f_both", "observe", false)).hasMessage("PAPER_ACCOUNT_REQUIRED");
        assertThatThrownBy(() -> service.bind("u1", "hf001_f_both", "live", false)).hasMessage("INVALID_STAGE");
        assertThatThrownBy(() -> service.bind("u1", "bad id", "observe", false)).hasMessage("INVALID_STRATEGY_ID");
        when(signals.findByUserIdAndStatusIn(eq("u1"), anyList())).thenReturn(List.of(new TradeSignal()));
        assertThatThrownBy(() -> service.bind("u1", "hf001_f_both", "observe", false)).hasMessage("ACTIVE_LLM_PLANS_PRESENT");
        assertThat(f.accountRows).isEmpty();
    }

    @Test
    void aNewBindingStartsTheDrawdownFromTheCurrentEquity() {
        f.peakRows.put("binding-1", 150_000_000L); // history of the broker account before the binding
        when(f.peaks.existsById("binding-1")).thenAnswer(inv -> f.peakRows.containsKey("binding-1"));
        doAnswer(inv -> f.peakRows.remove(inv.<String>getArgument(0))).when(f.peaks).deleteById(anyString());
        service.bind("u1", "hf001_f_both", "paper", true);
        assertThat(f.peakRows).doesNotContainKey("binding-1");
        StrategyRiskGuard.RiskState state = f.risk.evaluate("u1");
        assertThat(state.peakEquityKrw()).isEqualTo(100_000_000L);
        assertThat(state.reasons()).doesNotContain("DRAWDOWN_STOP");
        // Updating the stage keeps the drawdown history.
        f.peakRows.put("binding-1", 150_000_000L);
        service.bind("u1", "hf001_f_both", "paper", false);
        assertThat(f.peakRows).containsEntry("binding-1", 150_000_000L);
    }

    @Test
    void unbindIsRefusedWhileAnyOrderMayBeOpen() {
        service.bind("u1", "hf001_f_both", "paper", true);
        StrategyBatch batch = f.queued(StrategyTestFixture.basket());
        batch.setStatus("PARTIAL"); // not a live batch status, but an order is still open
        f.order(batch, "005930").setStatus("FILLED");
        f.order(batch, "0126Z0").setStatus("UNKNOWN");
        assertThatThrownBy(() -> service.unbind("u1")).hasMessage("STRATEGY_ORDERS_OPEN");
        f.order(batch, "0126Z0").setStatus("PARTIALLY_FILLED");
        batch.setStatus("HALTED");
        assertThatThrownBy(() -> service.unbind("u1")).hasMessage("STRATEGY_ORDERS_OPEN");
        f.order(batch, "0126Z0").setStatus("CANCELLED");
        assertThat(service.unbind("u1")).containsEntry("unbound", true);
    }

    @Test
    void unbindIsRefusedWhileABatchIsInProgress() {
        service.bind("u1", "hf001_f_both", "paper", true);
        when(batches.existsByUserIdAndStatusIn(eq("u1"), anyList())).thenReturn(true);
        assertThatThrownBy(() -> service.unbind("u1")).hasMessage("STRATEGY_BATCH_IN_PROGRESS");
        when(batches.existsByUserIdAndStatusIn(eq("u1"), anyList())).thenReturn(false);
        assertThat(service.unbind("u1")).containsEntry("unbound", true);
        verify(f.accounts).delete(any());
        assertThatThrownBy(() -> service.unbind("nobody")).hasMessage("STRATEGY_ACCOUNT_NOT_FOUND");
        assertThat(Optional.empty()).isEmpty();
    }
}
