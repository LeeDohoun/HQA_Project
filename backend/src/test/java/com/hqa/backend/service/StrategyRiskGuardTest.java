package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class StrategyRiskGuardTest {
    private final StrategyTestFixture f = new StrategyTestFixture();

    @Test
    void firstEvaluationStoresThePeakAndLaterOnesNeverLowerIt() {
        StrategyRiskGuard.RiskState first = f.risk.evaluate("u1");
        assertThat(first.peakEquityKrw()).isEqualTo(100_000_000L);
        assertThat(first.drawdownPct()).isZero();
        assertThat(first.buyAllowed()).isTrue();
        assertThat(first.reasons()).isEmpty();
        verify(f.peaks).raisePeak(eq("binding-1"), eq(100_000_000L), any());

        f.snapshot.put("equity", 95_000_000L);
        StrategyRiskGuard.RiskState second = f.risk.evaluate("u1");
        assertThat(second.peakEquityKrw()).isEqualTo(100_000_000L);
        assertThat(second.drawdownPct()).isCloseTo(5.0, within(1e-9));
        assertThat(f.peakRows).containsEntry("binding-1", 100_000_000L);

        f.snapshot.put("equity", 104_000_000L);
        assertThat(f.risk.evaluate("u1").peakEquityKrw()).isEqualTo(104_000_000L);
        assertThat(f.peakRows).containsEntry("binding-1", 104_000_000L);
    }

    @Test
    void dailyLossAtTheLimitBlocksBuys() {
        f.snapshot.put("dailyPnlPct", -1.99);
        assertThat(f.risk.evaluate("u1").buyAllowed()).isTrue();
        f.snapshot.put("dailyPnlPct", -2.0);
        StrategyRiskGuard.RiskState state = f.risk.evaluate("u1");
        assertThat(state.buyAllowed()).isFalse();
        assertThat(state.reasons()).containsExactly("DAILY_LOSS_LIMIT_EXCEEDED");
        assertThat(state.buyBlockReason()).isEqualTo("DAILY_LOSS_LIMIT_EXCEEDED");
    }

    @Test
    void drawdownStopAndFullReviewThresholds() {
        f.peakRows.put("binding-1", 111_111_000L); // 9.9999%
        assertThat(f.risk.evaluate("u1").buyAllowed()).isTrue();
        f.peakRows.put("binding-1", 111_111_200L); // 10.00007%
        StrategyRiskGuard.RiskState stop = f.risk.evaluate("u1");
        assertThat(stop.buyAllowed()).isFalse();
        assertThat(stop.fullReviewRequired()).isFalse();
        assertThat(stop.reasons()).containsExactly("DRAWDOWN_STOP");
        f.peakRows.put("binding-1", 120_000_000L); // 16.7%
        StrategyRiskGuard.RiskState review = f.risk.evaluate("u1");
        assertThat(review.fullReviewRequired()).isTrue();
        assertThat(review.reasons()).containsExactly("DRAWDOWN_STOP", "FULL_REVIEW_REQUIRED");
    }

    @Test
    void haltsUnknownBaselinesAndUnreadableAccountsBlockBuys() {
        f.halt("u1");
        StrategyRiskGuard.RiskState halted = f.risk.evaluate("u1");
        assertThat(halted.halted()).isTrue();
        assertThat(halted.buyAllowed()).isFalse();
        assertThat(halted.buyBlockReason()).isEqualTo("TRADING_HALTED");
        f.haltRows.clear();

        f.snapshot.put("dailyPnlPct", null);
        StrategyRiskGuard.RiskState noBaseline = f.risk.evaluate("u1");
        assertThat(noBaseline.buyAllowed()).isFalse();
        assertThat(noBaseline.reasons()).containsExactly("DAILY_BASELINE_UNAVAILABLE");

        f.snapshot.clear();
        f.snapshot.putAll(Map.of("success", false, "error", "KIS_TOKEN_UNAVAILABLE"));
        StrategyRiskGuard.RiskState unreadable = f.risk.evaluate("u1");
        assertThat(unreadable.buyAllowed()).isFalse();
        assertThat(unreadable.equityKrw()).isNull();
        assertThat(unreadable.reasons()).containsExactly("ACCOUNT_SNAPSHOT_UNAVAILABLE");
        verify(f.peaks, times(2)).raisePeak(anyString(), anyLong(), any());
    }

    @Test
    void riskMapHasTheDocumentedShape() {
        f.snapshot.put("dailyPnlPct", -0.4);
        f.peakRows.put("binding-1", 101_000_000L);
        Map<String, Object> map = f.risk.evaluate("u1").toMap();
        assertThat(map.keySet()).containsExactly("userId", "equityKrw", "dailyPnlPct", "peakEquityKrw", "drawdownPct",
                "buyAllowed", "fullReviewRequired", "halted", "reasons", "ordersToday", "cancelsToday", "limits");
        assertThat(map).containsEntry("equityKrw", 100_000_000L).containsEntry("dailyPnlPct", -0.4)
                .containsEntry("drawdownPct", 0.9901).containsEntry("reasons", List.of()).containsEntry("ordersToday", 0L);
        @SuppressWarnings("unchecked") Map<String, Object> limits = (Map<String, Object>) map.get("limits");
        assertThat(limits).containsEntry("dailyLossLimitPct", 2.0).containsEntry("drawdownStopPct", 10.0)
                .containsEntry("fullReviewPct", 15.0).containsEntry("maxOrdersPerDay", 1500).containsEntry("maxCancelRatio", 0.5)
                .containsEntry("maxOrderPctOfEquity", 10.0);
    }

    @Test
    void haltSwitchIsStoredPerScope() {
        Map<String, Object> view = f.risk.setHalt("GLOBAL", true, " maintenance ");
        assertThat(view).containsEntry("scope", "GLOBAL").containsEntry("halted", true).containsEntry("reason", "maintenance");
        assertThat(f.risk.isHalted("anyone")).isTrue();
        f.risk.setHalt("GLOBAL", false, null);
        assertThat(f.risk.isHalted("u1")).isFalse();
        assertThatThrownBy(() -> f.risk.setHalt(" ", true, null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> f.risk.setHalt("u1", null, null)).isInstanceOf(IllegalArgumentException.class);
    }
}
