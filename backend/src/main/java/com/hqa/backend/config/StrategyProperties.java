package com.hqa.backend.config;

import java.time.LocalDate;
import java.time.LocalTime;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * Strategy runtime settings ({@code hqa.strategy.*}). The risk limits are the backend's own and are
 * checked independently of the strategy code. Phase windows are regular-session times; on an official
 * special session they shift with the session's open (OPEN) or close (CLOSE). A window's end is exclusive.
 */
@Component
@ConfigurationProperties(prefix = "hqa.strategy")
public class StrategyProperties {
    public static final String MARKET = "MARKET";

    /** Master switch: when false the executor sends nothing and batches stay QUEUED. */
    private boolean ordersEnabled = false;
    private String orderType = MARKET;
    private LocalTime[] openWindow = window("08:30-08:59");
    private LocalTime[] closeWindow = window("15:20-15:29");
    private LocalTime openCancelAfter = LocalTime.of(9, 5);
    /** Continuous-session phases: SELL orders only, then BUY orders only; MARKET orders. */
    private LocalTime[] contSellWindow = window("09:05-09:20");
    private LocalTime[] contBuyWindow = window("09:20-10:00");
    /** When unfilled remainders of the continuous-session phases are cancelled. */
    private LocalTime contSellCancelAt = LocalTime.of(9, 20);
    private LocalTime contBuyCancelAt = LocalTime.of(10, 0);
    /** When the close-of-session NAV is recorded for terminating profiles (shifted with the close). */
    private LocalTime navCloseRecordAt = LocalTime.of(15, 45);
    private long navClosePollMs = 60000;
    /** Largest allowed gap between the binding (krx_official) and snapshot close NAV, percent of the binding NAV. */
    private double navCrosscheckTolerancePct = 0.5;
    /** Per-strategy risk profiles; strategies without an entry use the defaults of {@link Profile}. */
    private Map<String, Profile> profiles = new HashMap<>();
    private long executorPollMs = 2000;
    private long reconcilePollMs = 20000;
    private double dailyLossLimitPct = 2.0;
    private double drawdownStopPct = 10.0;
    private double fullReviewPct = 15.0;
    private int maxOrdersPerDay = 1500;
    private double maxCancelRatio = 0.5;
    private double maxOrderPctOfEquity = 10.0;
    private int maxOrdersPerBatch = 1000;
    /** How long the executor reuses one risk evaluation (a full KIS balance read) between BUY orders. */
    private long riskRecheckSeconds = 300;
    /** Longest the executor sends in one scheduler tick, so other scheduled jobs are not starved. */
    private long executorTickBudgetMs = 10000;
    /** Last date the KRX calendar was reviewed; strategy batches fail closed after it. */
    private LocalDate calendarReviewedThrough = LocalDate.parse("2027-09-30");
    /** Dates with an announced but unconfigured special session (e.g. CSAT day); refused until configured. */
    private Set<LocalDate> pendingSpecialSessions = dates("2026-11-19");

    public static LocalTime[] window(String value) {
        String[] parts = value == null ? new String[0] : value.trim().split("-");
        if (parts.length != 2) throw new IllegalArgumentException("Strategy window must be HH:MM-HH:MM: " + value);
        LocalTime start = LocalTime.parse(parts[0].trim());
        LocalTime end = LocalTime.parse(parts[1].trim());
        if (!start.isBefore(end)) throw new IllegalArgumentException("Strategy window must start before it ends: " + value);
        return new LocalTime[] {start, end};
    }

    private static Set<LocalDate> dates(String value) {
        Set<LocalDate> result = new TreeSet<>();
        if (value == null || value.isBlank()) return result;
        for (String raw : value.trim().split("\\s*,\\s*")) result.add(LocalDate.parse(raw));
        return result;
    }

    /**
     * Per-strategy risk profile ({@code hqa.strategy.profiles.<strategyId>.*}). Defaults keep the
     * standard behaviour: daily-loss and drawdown buy blocks on, no termination. The halt switch,
     * per-order equity cap, daily order cap and cancel ratio apply to every profile.
     */
    public static class Profile {
        private boolean dailyLossBuyBlock = true;
        private boolean drawdownBuyBlock = true;
        private Double terminationDrawdownPct;
        /** Scheduled BB exits (CLOSE phase, SELL only) may skip the cancel-ratio limit. */
        private boolean cancelRatioExemptBbExits = false;

        public boolean isDailyLossBuyBlock() { return dailyLossBuyBlock; }
        public void setDailyLossBuyBlock(boolean value) { this.dailyLossBuyBlock = value; }
        public boolean isDrawdownBuyBlock() { return drawdownBuyBlock; }
        public void setDrawdownBuyBlock(boolean value) { this.drawdownBuyBlock = value; }
        public Double getTerminationDrawdownPct() { return terminationDrawdownPct; }
        public void setTerminationDrawdownPct(Double value) {
            if (value != null && (!Double.isFinite(value) || value <= 0 || value >= 100)) {
                throw new IllegalArgumentException("termination-drawdown-pct must be within (0, 100)");
            }
            this.terminationDrawdownPct = value;
        }

        public boolean isCancelRatioExemptBbExits() { return cancelRatioExemptBbExits; }
        public void setCancelRatioExemptBbExits(boolean value) { this.cancelRatioExemptBbExits = value; }

        public Map<String, Object> view() {
            Map<String, Object> view = new LinkedHashMap<>();
            view.put("dailyLossBuyBlock", dailyLossBuyBlock);
            view.put("drawdownBuyBlock", drawdownBuyBlock);
            view.put("terminationDrawdownPct", terminationDrawdownPct);
            view.put("cancelRatioExemptBbExits", cancelRatioExemptBbExits);
            return view;
        }
    }

    public Map<String, Profile> getProfiles() { return profiles; }
    public void setProfiles(Map<String, Profile> profiles) { this.profiles = profiles == null ? new HashMap<>() : profiles; }

    public Profile profile(String strategyId) {
        Profile profile = strategyId == null ? null : profiles.get(strategyId);
        return profile == null ? new Profile() : profile;
    }

    public Map<String, Object> limits() {
        return Map.of("dailyLossLimitPct", dailyLossLimitPct, "drawdownStopPct", drawdownStopPct,
                "fullReviewPct", fullReviewPct, "maxOrdersPerDay", maxOrdersPerDay,
                "maxCancelRatio", maxCancelRatio, "maxOrderPctOfEquity", maxOrderPctOfEquity);
    }

    public boolean isOrdersEnabled() { return ordersEnabled; }
    public void setOrdersEnabled(boolean ordersEnabled) { this.ordersEnabled = ordersEnabled; }
    public String getOrderType() { return orderType; }
    public void setOrderType(String orderType) {
        String value = orderType == null ? "" : orderType.trim().toUpperCase();
        if (!MARKET.equals(value)) throw new IllegalArgumentException("hqa.strategy.order-type supports MARKET only: " + orderType);
        this.orderType = value;
    }
    public String getOpenWindow() { return openWindow[0] + "-" + openWindow[1]; }
    public void setOpenWindow(String value) { this.openWindow = window(value); }
    public String getCloseWindow() { return closeWindow[0] + "-" + closeWindow[1]; }
    public void setCloseWindow(String value) { this.closeWindow = window(value); }
    public LocalTime[] openWindowTimes() { return openWindow.clone(); }
    public LocalTime[] closeWindowTimes() { return closeWindow.clone(); }
    public String getContSellWindow() { return contSellWindow[0] + "-" + contSellWindow[1]; }
    public void setContSellWindow(String value) { this.contSellWindow = window(value); }
    public String getContBuyWindow() { return contBuyWindow[0] + "-" + contBuyWindow[1]; }
    public void setContBuyWindow(String value) { this.contBuyWindow = window(value); }
    public LocalTime[] contSellWindowTimes() { return contSellWindow.clone(); }
    public LocalTime[] contBuyWindowTimes() { return contBuyWindow.clone(); }
    public String getContSellCancelAt() { return contSellCancelAt.toString(); }
    public void setContSellCancelAt(String value) { this.contSellCancelAt = LocalTime.parse(value.trim()); }
    public LocalTime contSellCancelAtTime() { return contSellCancelAt; }
    public String getContBuyCancelAt() { return contBuyCancelAt.toString(); }
    public void setContBuyCancelAt(String value) { this.contBuyCancelAt = LocalTime.parse(value.trim()); }
    public LocalTime contBuyCancelAtTime() { return contBuyCancelAt; }
    public String getNavCloseRecordAt() { return navCloseRecordAt.toString(); }
    public void setNavCloseRecordAt(String value) { this.navCloseRecordAt = LocalTime.parse(value.trim()); }
    public LocalTime navCloseRecordAtTime() { return navCloseRecordAt; }
    public double getNavCrosscheckTolerancePct() { return navCrosscheckTolerancePct; }
    public void setNavCrosscheckTolerancePct(double value) { this.navCrosscheckTolerancePct = positive(value, "nav-crosscheck-tolerance-pct"); }
    public long getNavClosePollMs() { return navClosePollMs; }
    public void setNavClosePollMs(long value) { this.navClosePollMs = positive(value, "nav-close-poll-ms"); }
    public String getOpenCancelAfter() { return openCancelAfter.toString(); }
    public LocalTime openCancelAfterTime() { return openCancelAfter; }
    public void setOpenCancelAfter(String value) { this.openCancelAfter = LocalTime.parse(value.trim()); }
    public long getExecutorPollMs() { return executorPollMs; }
    public void setExecutorPollMs(long executorPollMs) { this.executorPollMs = positive(executorPollMs, "executor-poll-ms"); }
    public long getReconcilePollMs() { return reconcilePollMs; }
    public void setReconcilePollMs(long reconcilePollMs) { this.reconcilePollMs = positive(reconcilePollMs, "reconcile-poll-ms"); }
    public double getDailyLossLimitPct() { return dailyLossLimitPct; }
    public void setDailyLossLimitPct(double value) { this.dailyLossLimitPct = positive(value, "daily-loss-limit-pct"); }
    public double getDrawdownStopPct() { return drawdownStopPct; }
    public void setDrawdownStopPct(double value) { this.drawdownStopPct = positive(value, "drawdown-stop-pct"); }
    public double getFullReviewPct() { return fullReviewPct; }
    public void setFullReviewPct(double value) { this.fullReviewPct = positive(value, "full-review-pct"); }
    public int getMaxOrdersPerDay() { return maxOrdersPerDay; }
    public void setMaxOrdersPerDay(int value) { this.maxOrdersPerDay = (int) positive(value, "max-orders-per-day"); }
    public double getMaxCancelRatio() { return maxCancelRatio; }
    public void setMaxCancelRatio(double value) {
        if (!Double.isFinite(value) || value < 0 || value > 1) throw new IllegalArgumentException("hqa.strategy.max-cancel-ratio must be within 0..1");
        this.maxCancelRatio = value;
    }
    public double getMaxOrderPctOfEquity() { return maxOrderPctOfEquity; }
    public void setMaxOrderPctOfEquity(double value) {
        if (!Double.isFinite(value) || value <= 0 || value > 100) throw new IllegalArgumentException("hqa.strategy.max-order-pct-of-equity must be within (0, 100]");
        this.maxOrderPctOfEquity = value;
    }
    public int getMaxOrdersPerBatch() { return maxOrdersPerBatch; }
    public void setMaxOrdersPerBatch(int value) { this.maxOrdersPerBatch = (int) positive(value, "max-orders-per-batch"); }
    public long getRiskRecheckSeconds() { return riskRecheckSeconds; }
    public void setRiskRecheckSeconds(long value) { this.riskRecheckSeconds = (long) positive(value, "risk-recheck-seconds"); }
    public long getExecutorTickBudgetMs() { return executorTickBudgetMs; }
    public void setExecutorTickBudgetMs(long value) { this.executorTickBudgetMs = (long) positive(value, "executor-tick-budget-ms"); }

    public String getCalendarReviewedThrough() { return calendarReviewedThrough.toString(); }
    public void setCalendarReviewedThrough(String value) { this.calendarReviewedThrough = LocalDate.parse(value.trim()); }
    public LocalDate calendarReviewedThroughDate() { return calendarReviewedThrough; }
    public String getPendingSpecialSessions() { return String.join(",", pendingSpecialSessions.stream().map(LocalDate::toString).toList()); }
    public void setPendingSpecialSessions(String value) { this.pendingSpecialSessions = dates(value); }
    public Set<LocalDate> pendingSpecialSessionDates() { return Set.copyOf(pendingSpecialSessions); }

    private static long positive(long value, String name) {
        if (value <= 0) throw new IllegalArgumentException("hqa.strategy." + name + " must be positive");
        return value;
    }
    private static double positive(double value, String name) {
        if (!Double.isFinite(value) || value <= 0) throw new IllegalArgumentException("hqa.strategy." + name + " must be positive");
        return value;
    }
}
