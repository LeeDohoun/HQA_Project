package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.config.StrategyProperties;
import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.StrategyNavClose;
import com.hqa.backend.repository.StrategyBatchRepository;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** pf001 risk profile: no daily-loss/drawdown buy blocks, close-to-close termination from binding closes. */
class StrategyTerminationTest {
    private final StrategyTestFixture f = new StrategyTestFixture();
    private StrategyAccount pf001;

    @BeforeEach
    void pf001Account() {
        StrategyProperties.Profile profile = new StrategyProperties.Profile();
        profile.setDailyLossBuyBlock(false);
        profile.setDrawdownBuyBlock(false);
        profile.setTerminationDrawdownPct(25.0);
        f.properties.getProfiles().put("pf001", profile);
        f.accountRows.clear();
        pf001 = new StrategyAccount("u1", "pf001", "paper", true, f.now());
        pf001.activate(OffsetDateTime.parse("2026-11-02T08:00:00+09:00"), 100_000_000L);
        f.accountRows.put("u1", pf001);
    }

    private Map<String, Object> official(String day, long nav) {
        return f.termination.recordOfficialClose("u1", LocalDate.parse(day), nav, nav / 2, nav - nav / 2, "krx_official", "h-" + day);
    }

    private StrategyBatchRequest pfRequest(String phase, List<StrategyBatchRequest.Order> lines) {
        return new StrategyBatchRequest("pf001", "u1", HASH, DECISION, f.now().toLocalDate(), phase,
                StrategyBatchService.idempotencyKey("pf001", HASH, f.now().toLocalDate().toString(), phase, "u1"), true,
                100_000_000L, lines, null);
    }

    @Test
    void profileDefaultsKeepTheBuyBlocksAndPf001DropsThem() {
        assertThat(f.properties.profile("hf001_f_both").isDailyLossBuyBlock()).isTrue();
        assertThat(f.properties.profile("hf001_f_both").getTerminationDrawdownPct()).isNull();
        f.snapshot.put("dailyPnlPct", -3.0);
        f.peakRows.put("binding-1", 150_000_000L); // 33% below the equity peak
        StrategyRiskGuard.RiskState state = f.risk.evaluate("u1");
        assertThat(state.buyAllowed()).isTrue();
        assertThat(state.fullReviewRequired()).isFalse();
        assertThat(state.reasons()).isEmpty();
        assertThat(state.toMap()).containsEntry("terminationState", "active").containsEntry("activationNavKrw", 100_000_000L)
                .containsEntry("profile", Map.of("dailyLossBuyBlock", false, "drawdownBuyBlock", false, "terminationDrawdownPct", 25.0));

        f.accountRows.put("u1", new StrategyAccount("u1", "hf001_f_both", "paper", true, f.now()));
        StrategyRiskGuard.RiskState standard = f.risk.evaluate("u1");
        assertThat(standard.buyAllowed()).isFalse();
        assertThat(standard.reasons()).contains("DAILY_LOSS_LIMIT_EXCEEDED", "DRAWDOWN_STOP", "FULL_REVIEW_REQUIRED");
        assertThat(standard.toMap()).containsEntry("terminationState", null).containsEntry("terminated", false);
        assertThatThrownBy(() -> official("2026-11-02", 1)).hasMessage("TERMINATION_PROFILE_NOT_SET");
    }

    @Test
    void bindingRecordsTheActivationNavOnceAndTerminatingProfilesNeedIt() {
        f.accountRows.clear();
        StrategyAccountService accounts = new StrategyAccountService(f.accounts, f.snapshots, mock(TradeSignalRepository.class),
                mock(TradeSignalExecutionRepository.class), mock(StrategyBatchRepository.class), f.orders, f.guard, f.peaks,
                f.sessions, f.properties, f.transactions);
        when(f.accounts.findByStrategyId(anyString())).thenReturn(java.util.Optional.empty());
        Map<String, Object> observe = accounts.bind("u1", "pf001", "shadow", false);
        assertThat(observe).containsEntry("activatedAt", null).containsEntry("activatedNow", false);
        Map<String, Object> enabled = accounts.bind("u1", "pf001", "paper", true);
        assertThat(enabled).containsEntry("activationNavKrw", 100_000_000L).containsEntry("activatedNow", true);
        f.snapshot.put("equity", 90_000_000L);
        assertThat(accounts.bind("u1", "pf001", "paper", true)).containsEntry("activationNavKrw", 100_000_000L)
                .containsEntry("activatedNow", false);
        when(f.accounts.findAllByOrderByStrategyIdAsc()).thenAnswer(inv -> List.copyOf(f.accountRows.values()));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> listed = (List<Map<String, Object>>) accounts.list().get("accounts");
        assertThat(listed).hasSize(1);
        assertThat(listed.get(0)).containsEntry("accountBinding", "binding-1").containsEntry("activationNavKrw", 100_000_000L)
                .containsKeys("activatedAt", "terminatedAt", "terminationReason");

        f.accountRows.clear();
        f.snapshot.clear();
        f.snapshot.putAll(Map.of("success", false, "error", "KIS_TOKEN_UNAVAILABLE"));
        assertThatThrownBy(() -> accounts.bind("u1", "pf001", "paper", true)).hasMessage("ACTIVATION_NAV_UNAVAILABLE");
        assertThat(f.accountRows).isEmpty();
        assertThat(accounts.bind("u1", "hf001_f_both", "paper", true)).containsEntry("activatedAt", null); // no termination profile
    }

    @Test
    void officialClosesAreIdempotentAndTerminateAtAQuarterBelowTheRunningMax() {
        f.clock.set("2026-11-04T08:00:00+09:00");
        assertThat(official("2026-11-02", 110_000_000L)).containsEntry("deduplicated", false).containsEntry("terminated", false);
        assertThat(official("2026-11-02", 110_000_000L)).containsEntry("deduplicated", true);
        assertThatThrownBy(() -> official("2026-11-02", 109_000_000L)).isInstanceOf(IllegalStateException.class)
                .hasMessage("NAV_CLOSE_CONFLICT");
        assertThatThrownBy(() -> f.termination.recordOfficialClose("u1", LocalDate.parse("2026-11-02"), 110_000_000L, 1L, 1L,
                "krx_official", "other-hash")).hasMessage("NAV_CLOSE_CONFLICT");
        assertThatThrownBy(() -> official("2026-11-04", 1L)).hasMessage("SESSION_NOT_CLOSED");
        assertThatThrownBy(() -> official("2026-10-30", 1L)).hasMessage("NAV_CLOSE_BEFORE_ACTIVATION");
        assertThatThrownBy(() -> f.termination.recordOfficialClose("u1", LocalDate.parse("2026-11-03"), 1L, null, null, "snapshot", null))
                .isInstanceOf(IllegalArgumentException.class);

        Map<String, Object> above = official("2026-11-03", 82_500_001L); // 0.75 x 110M = 82.5M
        assertThat(above).containsEntry("terminated", false).containsEntry("closeNavMaxKrw", 110_000_000L);
        assertThat(pf001.isTerminated()).isFalse();
        f.clock.set("2026-11-05T08:00:00+09:00");
        Map<String, Object> breach = official("2026-11-04", 82_500_000L);
        assertThat(breach).containsEntry("terminated", true).containsEntry("terminationReason", "TERMINATION_DRAWDOWN:25");
        assertThat(pf001.getTerminatedAt()).isEqualTo(f.now());
        OffsetDateTime terminatedAt = pf001.getTerminatedAt();
        f.clock.set("2026-11-06T08:00:00+09:00");
        official("2026-11-05", 200_000_000L); // no automatic clearing
        assertThat(pf001.getTerminatedAt()).isEqualTo(terminatedAt);
    }

    @Test
    void theActivationNavCountsInTheRunningMax() {
        pf001 = new StrategyAccount("u1", "pf001", "paper", true, f.now());
        pf001.activate(OffsetDateTime.parse("2026-11-02T08:00:00+09:00"), 120_000_000L);
        f.accountRows.put("u1", pf001);
        f.clock.set("2026-11-03T08:00:00+09:00");
        assertThat(official("2026-11-02", 90_000_000L)).containsEntry("terminated", true); // 90M = 0.75 x 120M
    }

    @Test
    void aMissingBindingCloseBlocksBuysButNotSells() {
        assertThat(f.risk.evaluate("u1").reasons()).doesNotContain("NAV_CLOSE_MISSING"); // activation day: none required
        f.clock.set("2026-11-03T09:21:00+09:00");
        StrategyRiskGuard.RiskState state = f.risk.evaluate("u1");
        assertThat(state.buyAllowed()).isFalse();
        assertThat(state.reasons()).containsExactly("NAV_CLOSE_MISSING");
        Map<String, Object> buys = f.service.create(pfRequest("CONT_BUY", List.of(buy(1, "0126Z0", 1, 51_000))));
        assertThat(buys.get("reasons")).isEqualTo(List.of("NAV_CLOSE_MISSING"));
        f.clock.set("2026-11-03T09:06:00+09:00");
        assertThat(f.service.create(pfRequest("CONT_SELL", List.of(sell(1, "005930", 1, 70_000))))).containsEntry("status", "VALIDATED");
        official("2026-11-02", 101_000_000L);
        f.clock.set("2026-11-03T09:21:00+09:00");
        assertThat(f.service.create(pfRequest("CONT_BUY", List.of(buy(1, "0126Z0", 1, 51_000))))).containsEntry("status", "VALIDATED");
    }

    @Test
    void snapshotClosesAreRecordedOnceAfterTheCloseAndCrossCheckTheBindingClose() {
        f.snapshot.put("orderableCash", 55_000_000L); // D+0 deposit: not used for the close
        f.snapshot.put("settledCashD2Krw", 40_000_000L);
        f.snapshot.put("holdings", List.of(Map.of("stockCode", "005930", "quantity", 10L, "evalAmount", 60_000_000L)));
        f.clock.set("2026-11-02T15:44:00+09:00");
        f.termination.recordSnapshotCloses();
        assertThat(f.closeRows).isEmpty();
        f.clock.set("2026-11-02T15:45:00+09:00");
        f.termination.recordSnapshotCloses();
        f.termination.recordSnapshotCloses();
        verify(f.snapshots, times(1)).snapshot("u1");
        StrategyNavClose snapshotClose = f.closeRows.get(new StrategyNavClose.Key("binding-1", SESSION, "snapshot"));
        assertThat(snapshotClose.getNavKrw()).isEqualTo(100_000_000L);
        assertThat(snapshotClose.getCashKrw()).isEqualTo(40_000_000L);
        assertThat(snapshotClose.getHoldingsValueKrw()).isEqualTo(60_000_000L);
        assertThat(pf001.isTerminated()).isFalse(); // snapshot closes never terminate

        f.clock.set("2026-11-03T08:00:00+09:00");
        official("2026-11-02", 99_000_000L); // 1.01% apart > 0.5%
        StrategyRiskGuard.RiskState mismatch = f.risk.evaluate("u1");
        assertThat(mismatch.buyAllowed()).isFalse();
        assertThat(mismatch.reasons()).containsExactly("NAV_CROSSCHECK_MISMATCH");
        @SuppressWarnings("unchecked")
        Map<String, Object> crosscheck = (Map<String, Object>) mismatch.toMap().get("navCrosscheck");
        assertThat(crosscheck).containsEntry("mismatch", true).containsEntry("sessionDate", "2026-11-02");

        f.clock.set("2026-11-03T15:46:00+09:00");
        f.snapshot.put("settledCashD2Krw", 38_000_000L); // 38M + 60M holdings = 98M
        f.termination.recordSnapshotCloses();
        f.clock.set("2026-11-04T08:00:00+09:00");
        official("2026-11-03", 98_200_000L); // 0.2% apart: a later agreeing session clears it
        assertThat(f.risk.evaluate("u1").buyAllowed()).isTrue();

        // Without D+2 settled cash the snapshot close is stored as unavailable and does not clear or trigger.
        f.clock.set("2026-11-04T15:50:00+09:00");
        f.snapshot.remove("settledCashD2Krw");
        f.termination.recordSnapshotCloses();
        StrategyNavClose unavailable = f.closeRows.get(new StrategyNavClose.Key("binding-1", LocalDate.parse("2026-11-04"), "snapshot"));
        assertThat(unavailable.getNavKrw()).isNull();
        assertThat(unavailable.getCashKrw()).isNull();
        f.clock.set("2026-11-05T08:00:00+09:00");
        official("2026-11-04", 97_000_000L); // nothing to compare with
        assertThat(f.risk.evaluate("u1").reasons()).doesNotContain("NAV_CROSSCHECK_MISMATCH");
    }

    @Test
    void aTerminatedAccountOnlyLiquidates() {
        f.clock.set("2026-11-03T08:00:00+09:00");
        official("2026-11-02", 70_000_000L);
        assertThat(pf001.isTerminated()).isTrue();
        f.clock.set("2026-11-03T09:21:00+09:00");
        Map<String, Object> buys = f.service.create(pfRequest("CONT_BUY", List.of(buy(1, "0126Z0", 1, 51_000))));
        assertThat(buys.get("reasons")).isEqualTo(List.of("STRATEGY_TERMINATED"));
        assertThat(buys.get("rejectedOrders")).isEqualTo(List.of(Map.of("seq", 1, "reason", "STRATEGY_TERMINATED")));
        f.clock.set("2026-11-03T09:06:00+09:00");
        assertThat(f.service.create(pfRequest("CONT_SELL", List.of(sell(1, "005930", 1, 70_000))))).containsEntry("status", "VALIDATED");

        f.snapshot.put("holdings", List.of(Map.of("stockCode", "005930", "quantity", 10L, "evalAmount", 700_000L)));
        Map<String, Object> risk = f.risk.evaluate("u1").toMap();
        assertThat(risk).containsEntry("terminated", true).containsEntry("terminationState", "liquidating")
                .containsEntry("terminationReason", "TERMINATION_DRAWDOWN:25").containsEntry("closeNavMaxKrw", 100_000_000L)
                .containsEntry("closeDrawdownPct", 30.0).containsEntry("buyAllowed", false);
        f.snapshot.put("holdings", List.of());
        assertThat(f.risk.evaluate("u1").toMap()).containsEntry("terminationState", "terminated_drawdown");

        StrategyAccountService accounts = new StrategyAccountService(f.accounts, f.snapshots, mock(TradeSignalRepository.class),
                mock(TradeSignalExecutionRepository.class), mock(StrategyBatchRepository.class), f.orders, f.guard, f.peaks,
                f.sessions, f.properties, f.transactions);
        assertThatThrownBy(() -> accounts.unbind("u1")).hasMessage("STRATEGY_TERMINATED_CANNOT_UNBIND");
    }
}
