package com.hqa.backend.service;

import com.hqa.backend.config.StrategyProperties;
import com.hqa.backend.entity.AccountEquityPeak;
import com.hqa.backend.entity.TradingHalt;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.AccountEquityPeakRepository;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.TradingHaltRepository;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Backend risk limits for strategy accounts, independent of the strategy code: daily loss and
 * drawdown block buys, the halt switch blocks everything, and a deep drawdown flags a full review.
 */
@Service
public class StrategyRiskGuard {
    private static final Logger log = LoggerFactory.getLogger(StrategyRiskGuard.class);
    /** Reasons that block BUY orders only; sells stay allowed. */
    public static final Set<String> BUY_BLOCK_REASONS = Set.of("DAILY_LOSS_LIMIT_EXCEEDED", "DRAWDOWN_STOP",
            "DAILY_BASELINE_UNAVAILABLE", "ACCOUNT_SNAPSHOT_UNAVAILABLE");
    private final PaperAccountSnapshotService snapshots;
    private final AccountEquityPeakRepository peaks;
    private final StrategyBatchOrderRepository orders;
    private final TradingHaltRepository halts;
    private final StrategyProperties properties;
    private final StrategySessionClock sessions;

    public StrategyRiskGuard(PaperAccountSnapshotService snapshots, AccountEquityPeakRepository peaks,
            StrategyBatchOrderRepository orders, TradingHaltRepository halts, StrategyProperties properties,
            StrategySessionClock sessions) {
        this.snapshots = snapshots;
        this.peaks = peaks;
        this.orders = orders;
        this.halts = halts;
        this.properties = properties;
        this.sessions = sessions;
    }

    public record RiskState(String userId, Long equityKrw, Double dailyPnlPct, Long peakEquityKrw, Double drawdownPct,
            boolean buyAllowed, boolean fullReviewRequired, boolean halted, List<String> reasons, long ordersToday,
            long cancelsToday, Map<String, Object> limits) {
        public boolean snapshotAvailable() {
            return equityKrw != null;
        }

        /** The first reason that blocks buys, for a skipped or rejected BUY order. */
        public String buyBlockReason() {
            if (halted) return "TRADING_HALTED";
            return reasons.stream().filter(BUY_BLOCK_REASONS::contains).findFirst().orElse(buyAllowed ? null : "BUY_NOT_ALLOWED");
        }

        public Map<String, Object> toMap() {
            Map<String, Object> map = new LinkedHashMap<>();
            map.put("userId", userId);
            map.put("equityKrw", equityKrw);
            map.put("dailyPnlPct", round(dailyPnlPct));
            map.put("peakEquityKrw", peakEquityKrw);
            map.put("drawdownPct", round(drawdownPct));
            map.put("buyAllowed", buyAllowed);
            map.put("fullReviewRequired", fullReviewRequired);
            map.put("halted", halted);
            map.put("reasons", reasons);
            map.put("ordersToday", ordersToday);
            map.put("cancelsToday", cancelsToday);
            map.put("limits", limits);
            return map;
        }
    }

    /** Reads a fresh PAPER balance (one KIS balance inquiry) and raises the stored equity peak. */
    public RiskState evaluate(String userId) {
        OffsetDateTime now = sessions.now();
        LocalDate today = now.toLocalDate();
        List<String> reasons = new ArrayList<>();
        boolean halted = isHalted(userId);
        if (halted) reasons.add("TRADING_HALTED");
        long ordersToday = orders.countOrdersForUserOn(userId, today);
        long cancelsToday = orders.countCancelsForUserOn(userId, today);
        if (ordersToday >= properties.getMaxOrdersPerDay()) reasons.add("DAILY_ORDER_LIMIT_EXCEEDED");
        if (cancelRatioExceeded(ordersToday, cancelsToday)) reasons.add("CANCEL_RATIO_EXCEEDED");
        Map<String, Object> snapshot = snapshots.snapshot(userId);
        Long equity = null;
        Double dailyPnl = null;
        Long peak = null;
        Double drawdown = null;
        try {
            if (!Boolean.TRUE.equals(snapshot.get("success")) || !(snapshot.get("equity") instanceof Number value)
                    || value.longValue() <= 0) {
                throw new IllegalStateException(String.valueOf(snapshot.get("error")));
            }
            User user = snapshots.paperUser(userId);
            String binding = snapshots.binding(user);
            equity = value.longValue();
            dailyPnl = snapshot.get("dailyPnlPct") instanceof Number pnl ? pnl.doubleValue() : null;
            long stored = peaks.findById(binding).map(AccountEquityPeak::getPeakEquityKrw).orElse(0L);
            peak = Math.max(stored, equity);
            peaks.raisePeak(binding, equity, now);
            drawdown = 100.0 * (peak - equity) / peak;
        } catch (RuntimeException ex) {
            log.warn("[StrategyRiskGuard] account snapshot unavailable for {}: {}", userId, ex.getMessage());
            equity = null;
            dailyPnl = null;
            reasons.add("ACCOUNT_SNAPSHOT_UNAVAILABLE");
        }
        if (equity != null) {
            if (dailyPnl == null) reasons.add("DAILY_BASELINE_UNAVAILABLE");
            else if (dailyPnl <= -properties.getDailyLossLimitPct()) reasons.add("DAILY_LOSS_LIMIT_EXCEEDED");
            if (drawdown >= properties.getDrawdownStopPct()) reasons.add("DRAWDOWN_STOP");
        }
        boolean fullReview = drawdown != null && drawdown >= properties.getFullReviewPct();
        if (fullReview) reasons.add("FULL_REVIEW_REQUIRED");
        boolean buyAllowed = !halted && equity != null && dailyPnl != null
                && dailyPnl > -properties.getDailyLossLimitPct() && drawdown < properties.getDrawdownStopPct();
        return new RiskState(userId, equity, dailyPnl, peak, drawdown, buyAllowed, fullReview, halted,
                List.copyOf(reasons), ordersToday, cancelsToday, properties.limits());
    }

    public boolean cancelRatioExceeded(long ordersToday, long cancelsToday) {
        return ordersToday > 0 && (double) cancelsToday / ordersToday > properties.getMaxCancelRatio();
    }

    /** Database-only check of the GLOBAL and account halt switches. */
    public boolean isHalted(String userId) {
        return halted(TradingHalt.GLOBAL) || (userId != null && halted(userId));
    }

    private boolean halted(String scope) {
        return halts.findById(scope).map(TradingHalt::isHalted).orElse(false);
    }

    @Transactional
    public Map<String, Object> setHalt(String scope, Boolean halted, String reason) {
        if (scope == null || scope.isBlank() || scope.length() > 255) throw new IllegalArgumentException("scope is required");
        if (halted == null) throw new IllegalArgumentException("halted is required");
        TradingHalt halt = halts.findById(scope.strip()).orElseGet(() -> new TradingHalt(scope.strip()));
        halt.setHalted(halted);
        halt.setReason(reason == null || reason.isBlank() ? null : reason.strip());
        halt.setUpdatedAt(sessions.now());
        return view(halts.save(halt));
    }

    public List<Map<String, Object>> halts() {
        return halts.findAllByOrderByScopeAsc().stream().map(StrategyRiskGuard::view).toList();
    }

    public static Map<String, Object> view(TradingHalt halt) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("scope", halt.getScope());
        row.put("halted", halt.isHalted());
        row.put("reason", halt.getReason());
        row.put("updatedAt", halt.getUpdatedAt());
        return row;
    }

    private static Double round(Double value) {
        return value == null ? null : Math.round(value * 10_000.0) / 10_000.0;
    }
}
