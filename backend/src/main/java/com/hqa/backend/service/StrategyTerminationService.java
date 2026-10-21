package com.hqa.backend.service;

import com.hqa.backend.config.StrategyProperties;
import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.StrategyNavClose;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyNavCloseRepository;
import jakarta.annotation.PreDestroy;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

/**
 * Close-to-close drawdown termination for strategies whose risk profile sets termination-drawdown-pct
 * (pf001). Termination is decided only from the binding {@code krx_official} closes posted by the Python
 * runtime: breach when close NAV <= (1 - pct/100) x running max of the activation NAV and every binding
 * close since activation. The backend's own after-close {@code snapshot} closes are a cross-check. A
 * termination is recorded once and never cleared. Other strategies are not affected by anything here.
 */
@Service
public class StrategyTerminationService {
    private static final Logger log = LoggerFactory.getLogger(StrategyTerminationService.class);
    public static final List<String> LIVE_ORDER_STATUSES = List.of("PENDING", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN");
    private final StrategyAccountRepository accounts;
    private final StrategyNavCloseRepository closes;
    private final StrategyBatchOrderRepository batchOrders;
    private final PaperAccountSnapshotService snapshots;
    private final StrategySessionClock sessions;
    private final StrategyProperties properties;
    private final StrategyWorker worker = new StrategyWorker("strategy-nav-close-recorder");

    public StrategyTerminationService(StrategyAccountRepository accounts, StrategyNavCloseRepository closes,
            StrategyBatchOrderRepository batchOrders, PaperAccountSnapshotService snapshots, StrategySessionClock sessions,
            StrategyProperties properties) {
        this.accounts = accounts;
        this.closes = closes;
        this.batchOrders = batchOrders;
        this.snapshots = snapshots;
        this.sessions = sessions;
        this.properties = properties;
    }

    /** Termination facts for one account; {@code applies} is false for profiles without termination. */
    public record TerminationView(boolean applies, boolean terminated, OffsetDateTime terminatedAt, String terminationReason,
            OffsetDateTime activatedAt, Long activationNavKrw, Long closeNavMaxKrw, Double closeDrawdownPct,
            LocalDate latestOfficialCloseDate, LocalDate requiredCloseDate, boolean navCloseMissing,
            Map<String, Object> navCrosscheck, boolean crosscheckMismatch) {

        /** Reasons that block BUY orders (sells stay allowed). */
        public List<String> buyBlockReasons() {
            List<String> reasons = new ArrayList<>();
            if (!applies) return reasons;
            if (terminated) reasons.add("STRATEGY_TERMINATED");
            if (navCloseMissing) reasons.add("NAV_CLOSE_MISSING");
            if (crosscheckMismatch) reasons.add("NAV_CROSSCHECK_MISMATCH");
            return reasons;
        }

        /** active | liquidating (terminated, holdings or open orders remain) | terminated_drawdown; null if not applicable. */
        public String state(Boolean holdingsOrOpenOrders) {
            if (!applies) return null;
            if (!terminated) return "active";
            return Boolean.FALSE.equals(holdingsOrOpenOrders) ? "terminated_drawdown" : "liquidating";
        }
    }

    public boolean applies(String strategyId) {
        return properties.profile(strategyId).getTerminationDrawdownPct() != null;
    }

    /** Database-only termination view of an account (binding = its PAPER account fingerprint). */
    public TerminationView view(StrategyAccount account, String binding, OffsetDateTime now) {
        if (account == null || !applies(account.getStrategyId())) {
            return new TerminationView(false, false, null, null, null, null, null, null, null, null, false, null, false);
        }
        LocalDate from = account.getActivatedAt() == null ? LocalDate.MIN
                : account.getActivatedAt().atZoneSameInstant(StrategySessionClock.KST).toLocalDate();
        List<StrategyNavClose> rows = binding == null ? List.of()
                : closes.findByAccountBindingAndSessionDateGreaterThanEqualOrderBySessionDateAsc(binding, from);
        List<StrategyNavClose> official = rows.stream().filter(row -> StrategyNavClose.OFFICIAL.equals(row.getSource())).toList();
        Long max = account.getActivationNavKrw();
        for (StrategyNavClose row : official) max = max == null ? row.getNavKrw() : Math.max(max, row.getNavKrw());
        StrategyNavClose latest = official.isEmpty() ? null : official.get(official.size() - 1);
        Double drawdown = latest == null || max == null ? null : 100.0 * (max - latest.getNavKrw()) / max;
        LocalDate required = previousSession(now.toLocalDate());
        boolean missing = account.getActivatedAt() != null && required != null && !required.isBefore(from)
                && official.stream().noneMatch(row -> row.getSessionDate().equals(required));
        Map<String, Object> crosscheck = crosscheck(rows);
        boolean mismatch = crosscheck != null && Boolean.TRUE.equals(crosscheck.get("mismatch"));
        return new TerminationView(true, account.isTerminated(), account.getTerminatedAt(), account.getTerminationReason(),
                account.getActivatedAt(), account.getActivationNavKrw(), max, drawdown,
                latest == null ? null : latest.getSessionDate(), required, missing, crosscheck, mismatch);
    }

    /** The latest session with both closes decides: a later agreeing pair clears an earlier mismatch. */
    private Map<String, Object> crosscheck(List<StrategyNavClose> rows) {
        Map<LocalDate, StrategyNavClose[]> pairs = new TreeMap<>();
        for (StrategyNavClose row : rows) {
            if (row.getNavKrw() == null) continue; // an unavailable snapshot close neither confirms nor contradicts
            StrategyNavClose[] pair = pairs.computeIfAbsent(row.getSessionDate(), ignored -> new StrategyNavClose[2]);
            pair[StrategyNavClose.OFFICIAL.equals(row.getSource()) ? 0 : 1] = row;
        }
        Map.Entry<LocalDate, StrategyNavClose[]> latest = null;
        for (Map.Entry<LocalDate, StrategyNavClose[]> entry : pairs.entrySet()) {
            if (entry.getValue()[0] != null && entry.getValue()[1] != null) latest = entry;
        }
        if (latest == null) return null;
        long official = latest.getValue()[0].getNavKrw();
        long snapshot = latest.getValue()[1].getNavKrw();
        double diffPct = 100.0 * Math.abs(official - snapshot) / official;
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("sessionDate", latest.getKey().toString());
        view.put("officialNavKrw", official);
        view.put("snapshotNavKrw", snapshot);
        view.put("diffPct", Math.round(diffPct * 10_000.0) / 10_000.0);
        view.put("tolerancePct", properties.getNavCrosscheckTolerancePct());
        view.put("mismatch", diffPct > properties.getNavCrosscheckTolerancePct());
        return view;
    }

    /** Most recent KRX session strictly before the day, or null if none within 30 days. */
    LocalDate previousSession(LocalDate day) {
        LocalDate candidate = day.minusDays(1);
        for (int i = 0; i < 30; i++, candidate = candidate.minusDays(1)) {
            if (sessions.isSessionDay(candidate)) return candidate;
        }
        return null;
    }

    /** True while the terminated account still holds shares or has strategy orders that may be open. */
    public Boolean holdingsOrOpenOrders(String userId, Map<String, Object> snapshot) {
        if (batchOrders.existsOrderForUserWithStatusIn(userId, LIVE_ORDER_STATUSES)) return true;
        if (snapshot == null || !Boolean.TRUE.equals(snapshot.get("success")) || !(snapshot.get("holdings") instanceof List<?> rows)) {
            return null; // unknown
        }
        return rows.stream().anyMatch(row -> row instanceof Map<?, ?> map && map.get("quantity") instanceof Number n && n.longValue() > 0);
    }

    /**
     * Stores the binding close NAV posted by the Python runtime. Same content for a session returns the
     * stored row (deduplicated); different content is a conflict. Termination is re-evaluated each time.
     */
    public Map<String, Object> recordOfficialClose(String userId, LocalDate sessionDate, Long navKrw, Long cashKrw,
            Long holdingsValueKrw, String source, String inputHash) {
        StrategyAccount account = accounts.findById(userId).orElseThrow(() -> new IllegalArgumentException("STRATEGY_ACCOUNT_NOT_FOUND"));
        if (!applies(account.getStrategyId())) throw new IllegalArgumentException("TERMINATION_PROFILE_NOT_SET");
        if (!StrategyNavClose.OFFICIAL.equals(source)) throw new IllegalArgumentException("source must be krx_official");
        if (sessionDate == null || navKrw == null || navKrw <= 0) throw new IllegalArgumentException("sessionDate and a positive navKrw are required");
        if ((cashKrw != null && cashKrw < 0) || (holdingsValueKrw != null && holdingsValueKrw < 0)) {
            throw new IllegalArgumentException("cashKrw and holdingsValueKrw must not be negative");
        }
        if (inputHash != null && (inputHash.isBlank() || inputHash.length() > 128)) throw new IllegalArgumentException("INVALID_INPUT_HASH");
        if (account.getActivatedAt() == null) throw new IllegalStateException("STRATEGY_ACCOUNT_NOT_ACTIVATED");
        if (sessionDate.isBefore(account.getActivatedAt().atZoneSameInstant(StrategySessionClock.KST).toLocalDate())) {
            throw new IllegalArgumentException("NAV_CLOSE_BEFORE_ACTIVATION");
        }
        if (!sessions.isSessionDay(sessionDate)) throw new IllegalArgumentException("NOT_A_KRX_SESSION");
        OffsetDateTime now = sessions.now();
        OffsetDateTime close = sessions.sessionCloseAt(sessionDate);
        if (close == null || now.isBefore(close)) throw new IllegalArgumentException("SESSION_NOT_CLOSED");
        User user = snapshots.paperUser(userId);
        String binding = snapshots.binding(user);
        Optional<StrategyNavClose> stored = closes.findByAccountBindingAndSessionDateAndSource(binding, sessionDate, StrategyNavClose.OFFICIAL);
        boolean deduplicated = stored.isPresent();
        if (stored.isPresent() && !stored.get().sameContent(navKrw, cashKrw, holdingsValueKrw, inputHash)) {
            throw new IllegalStateException("NAV_CLOSE_CONFLICT");
        }
        if (stored.isEmpty()) {
            try {
                closes.saveAndFlush(new StrategyNavClose(binding, sessionDate, StrategyNavClose.OFFICIAL, navKrw, cashKrw,
                        holdingsValueKrw, inputHash, now));
            } catch (DataIntegrityViolationException ex) {
                StrategyNavClose raced = closes.findByAccountBindingAndSessionDateAndSource(binding, sessionDate, StrategyNavClose.OFFICIAL)
                        .orElseThrow(() -> ex);
                if (!raced.sameContent(navKrw, cashKrw, holdingsValueKrw, inputHash)) throw new IllegalStateException("NAV_CLOSE_CONFLICT");
                deduplicated = true;
            }
        }
        evaluate(account, binding, now);
        TerminationView view = view(account, binding, now);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("userId", userId);
        result.put("sessionDate", sessionDate.toString());
        result.put("source", StrategyNavClose.OFFICIAL);
        result.put("navKrw", navKrw);
        result.put("deduplicated", deduplicated);
        result.put("terminated", view.terminated());
        result.put("terminatedAt", view.terminatedAt());
        result.put("terminationReason", view.terminationReason());
        result.put("closeNavMaxKrw", view.closeNavMaxKrw());
        result.put("closeDrawdownPct", round(view.closeDrawdownPct()));
        return result;
    }

    /** Running max over the activation NAV and the binding closes in date order; the first breach terminates. */
    void evaluate(StrategyAccount account, String binding, OffsetDateTime now) {
        Double pct = properties.profile(account.getStrategyId()).getTerminationDrawdownPct();
        if (pct == null || account.isTerminated() || account.getActivatedAt() == null) return;
        LocalDate from = account.getActivatedAt().atZoneSameInstant(StrategySessionClock.KST).toLocalDate();
        BigDecimal keep = BigDecimal.valueOf(100).subtract(BigDecimal.valueOf(pct));
        Long max = account.getActivationNavKrw();
        for (StrategyNavClose row : closes.findByAccountBindingAndSessionDateGreaterThanEqualOrderBySessionDateAsc(binding, from)) {
            if (!StrategyNavClose.OFFICIAL.equals(row.getSource())) continue;
            max = max == null ? row.getNavKrw() : Math.max(max, row.getNavKrw());
            // Breach: close <= (1 - pct/100) x max, compared exactly.
            if (BigDecimal.valueOf(row.getNavKrw()).multiply(BigDecimal.valueOf(100))
                    .compareTo(BigDecimal.valueOf(max).multiply(keep)) <= 0) {
                account.terminate(now, "TERMINATION_DRAWDOWN:" + BigDecimal.valueOf(pct).stripTrailingZeros().toPlainString());
                account.setUpdatedAt(now);
                accounts.save(account);
                log.warn("[StrategyTermination] {} terminated: close {} on {} vs max {}", account.getStrategyId(),
                        row.getNavKrw(), row.getSessionDate(), max);
                return;
            }
        }
    }

    @Scheduled(fixedDelayString = "${hqa.strategy.nav-close-poll-ms:60000}")
    public void tick() {
        worker.submit(() -> {
            try {
                recordSnapshotCloses();
            } catch (RuntimeException ex) {
                log.error("[StrategyTermination] snapshot close recording failed: {}", ex.getMessage());
            }
        });
    }

    @PreDestroy
    public void shutdown() {
        worker.close();
    }

    /**
     * After the record time of a session, stores one snapshot close (KIS balance read) per activated account
     * with a terminating profile. Idempotent per session; a failed read is retried on the next poll.
     */
    public void recordSnapshotCloses() {
        OffsetDateTime now = sessions.now();
        LocalDate today = now.toLocalDate();
        if (!sessions.isSessionDay(today)) return;
        OffsetDateTime recordAt = sessions.navCloseRecordAt(today);
        if (recordAt == null || now.isBefore(recordAt)) return;
        for (StrategyAccount account : accounts.findAll()) {
            if (!applies(account.getStrategyId()) || account.getActivatedAt() == null) continue;
            try {
                String binding = snapshots.binding(snapshots.paperUser(account.getUserId()));
                if (closes.findByAccountBindingAndSessionDateAndSource(binding, today, StrategyNavClose.SNAPSHOT).isPresent()) continue;
                Map<String, Object> snapshot = snapshots.snapshot(account.getUserId());
                if (!Boolean.TRUE.equals(snapshot.get("success"))) {
                    log.warn("[StrategyTermination] snapshot close unavailable for {}: {}", account.getUserId(), snapshot.get("error"));
                    continue; // retried on the next poll
                }
                // NAV = D+2 settled cash + holdings at the close price (evaluation after the close). The D+0
                // deposit is not used: it does not reflect unsettled trades. Without D+2 cash the close is
                // stored as unavailable (null NAV), not filled in from another field.
                Long cash = snapshot.get("settledCashD2Krw") instanceof Number n ? n.longValue() : null;
                long holdings = 0;
                if (snapshot.get("holdings") instanceof List<?> rows) {
                    for (Object raw : rows) {
                        if (raw instanceof Map<?, ?> row && row.get("evalAmount") instanceof Number value) holdings += value.longValue();
                    }
                }
                Long nav = cash == null || cash + holdings <= 0 ? null : cash + holdings;
                if (nav == null) log.warn("[StrategyTermination] D+2 settled cash missing for {}; snapshot close unavailable", account.getUserId());
                closes.saveAndFlush(new StrategyNavClose(binding, today, StrategyNavClose.SNAPSHOT, nav, cash, holdings, null, now));
            } catch (DataIntegrityViolationException ex) {
                log.info("[StrategyTermination] snapshot close for {} already recorded", account.getUserId());
            } catch (RuntimeException ex) {
                log.warn("[StrategyTermination] snapshot close failed for {}: {}", account.getUserId(), ex.getMessage());
            }
        }
    }

    static Double round(Double value) {
        return value == null ? null : Math.round(value * 10_000.0) / 10_000.0;
    }
}
