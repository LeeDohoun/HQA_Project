package com.hqa.backend.service;

import com.hqa.backend.config.StrategyProperties;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyBatchRepository;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Reconciles today's submitted strategy orders with the KIS PAPER daily order list: fills and average
 * prices come only from the broker. Unfilled OPEN-phase quantity is cancelled after open-cancel-after;
 * quantity still open after the session close is recorded as expired. Batches end COMPLETED, PARTIAL
 * or REJECTED (HALTED batches keep their status). An account is not read while its batch is being sent,
 * nor an OPEN batch before the session opens. Batches of past sessions get one cleanup pass.
 * Runs on its own thread so slow paged order inquiries never delay the batch executor's thread.
 */
@Component
public class StrategyBatchReconciler {
    private static final Logger log = LoggerFactory.getLogger(StrategyBatchReconciler.class);
    private static final Set<String> TRACKED = Set.of("SUBMITTED", "PARTIALLY_FILLED");
    private final StrategyProperties properties;
    private final StrategyBatchRepository batches;
    private final StrategyBatchOrderRepository batchOrders;
    private final StrategyBatchService batchService;
    private final StrategySessionClock sessions;
    private final PaperAccountSnapshotService snapshots;
    private final KisClient kis;
    private final StrategyWorker worker = new StrategyWorker("strategy-batch-reconciler");

    public StrategyBatchReconciler(StrategyProperties properties, StrategyBatchRepository batches,
            StrategyBatchOrderRepository batchOrders, StrategyBatchService batchService, StrategySessionClock sessions,
            PaperAccountSnapshotService snapshots, KisClient kis) {
        this.properties = properties;
        this.batches = batches;
        this.batchOrders = batchOrders;
        this.batchService = batchService;
        this.sessions = sessions;
        this.snapshots = snapshots;
        this.kis = kis;
    }

    @Scheduled(fixedDelayString = "${hqa.strategy.reconcile-poll-ms:20000}")
    public void tick() {
        worker.submit(this::safeRun);
    }

    void safeRun() {
        try {
            runOnce();
        } catch (RuntimeException ex) {
            log.error("[StrategyBatchReconciler] reconciliation failed: {}", ex.getMessage());
        }
    }

    @PreDestroy
    public void shutdown() {
        worker.close();
    }

    public void runOnce() {
        OffsetDateTime now = sessions.now();
        LocalDate today = now.toLocalDate();
        if (!properties.isOrdersEnabled()) {
            // The executor is not running in this process; close batches a previous run left SUBMITTING.
            for (StrategyBatch batch : batches.findBySessionDateAndStatusInOrderByCreatedAtAsc(today, List.of("SUBMITTING"))) {
                if (sessions.phaseWindowClosed(batch.getSessionDate(), batch.getPhase(), now)) batchService.closeWindow(batch, now);
            }
        }
        Set<String> sending = sendingUsers(today, now);
        List<StrategyBatch> active = batches.findBySessionDateAndStatusInOrderByCreatedAtAsc(today,
                List.of("SUBMITTED", "PARTIAL", "HALTED"));
        for (Map.Entry<String, List<StrategyBatch>> entry : byUser(active).entrySet()) {
            // Leave the account's KIS request budget to the executor while it is sending a batch.
            if (sending.contains(entry.getKey())) continue;
            try {
                reconcileUser(entry.getKey(), entry.getValue(), now);
            } catch (ObjectOptimisticLockingFailureException ex) {
                log.warn("[StrategyBatchReconciler] batch changed concurrently for {}; retrying next tick", entry.getKey());
            } catch (RuntimeException ex) {
                log.error("[StrategyBatchReconciler] reconciliation failed for {}: {}", entry.getKey(), ex.getMessage());
            }
        }
        for (Map.Entry<String, List<StrategyBatch>> entry : byUser(batches.findStaleBefore(today)).entrySet()) {
            if (sending.contains(entry.getKey())) continue;
            try {
                cleanupPastSessions(entry.getKey(), entry.getValue(), now);
            } catch (ObjectOptimisticLockingFailureException ex) {
                log.warn("[StrategyBatchReconciler] past batch changed concurrently for {}; retrying next tick", entry.getKey());
            } catch (RuntimeException ex) {
                log.error("[StrategyBatchReconciler] past-session cleanup failed for {}: {}", entry.getKey(), ex.getMessage());
            }
        }
    }

    /** Users whose QUEUED or SUBMITTING batch is inside its phase window while the executor runs. */
    private Set<String> sendingUsers(LocalDate today, OffsetDateTime now) {
        Set<String> users = new HashSet<>();
        if (!properties.isOrdersEnabled()) return users;
        for (StrategyBatch batch : batches.findBySessionDateAndStatusInOrderByCreatedAtAsc(today, List.of("QUEUED", "SUBMITTING"))) {
            if (sessions.inPhaseWindow(batch.getSessionDate(), batch.getPhase(), now)) users.add(batch.getUserId());
        }
        return users;
    }

    private static Map<String, List<StrategyBatch>> byUser(List<StrategyBatch> list) {
        Map<String, List<StrategyBatch>> result = new LinkedHashMap<>();
        for (StrategyBatch batch : list) result.computeIfAbsent(batch.getUserId(), ignored -> new ArrayList<>()).add(batch);
        return result;
    }

    void reconcileUser(String userId, List<StrategyBatch> userBatches, OffsetDateTime now) {
        Map<StrategyBatch, List<StrategyBatchOrder>> ordersByBatch = new LinkedHashMap<>();
        boolean tracked = false;
        for (StrategyBatch batch : userBatches) {
            OffsetDateTime open = sessions.sessionOpenAt(batch.getSessionDate());
            // Nothing fills before the session opens; an OPEN batch is not read until then.
            if ("OPEN".equals(batch.getPhase()) && open != null && now.isBefore(open)) continue;
            List<StrategyBatchOrder> orders = batchOrders.findByBatchIdOrderBySeqAsc(batch.getId());
            ordersByBatch.put(batch, orders);
            tracked |= orders.stream().anyMatch(StrategyBatchReconciler::tracked);
        }
        if (tracked) {
            User user = snapshots.paperUser(userId);
            String binding = snapshots.binding(user);
            String token = kis.fetchAccessToken(userId, user.getSecret());
            if (token == null || token.isBlank()) throw new IllegalStateException("KIS_TOKEN_UNAVAILABLE");
            List<Map<String, Object>> rows = kis.paperOrders(userId, user.getSecret(), token, now.toLocalDate(), now.toLocalDate());
            for (Map.Entry<StrategyBatch, List<StrategyBatchOrder>> entry : ordersByBatch.entrySet()) {
                StrategyBatch batch = entry.getKey();
                if (!binding.equals(batch.getAccountBinding())) {
                    log.error("[StrategyBatchReconciler] batch {} belongs to another PAPER account binding; not reconciled", batch.getId());
                    continue;
                }
                for (StrategyBatchOrder order : entry.getValue()) {
                    if (tracked(order)) reconcileOrder(batch, order, rows, user, token, now, false);
                }
            }
        }
        for (Map.Entry<StrategyBatch, List<StrategyBatchOrder>> entry : ordersByBatch.entrySet()) {
            batchService.finalizeStatus(entry.getKey(), entry.getValue(), now);
        }
    }

    /**
     * Batches of past sessions: unsent orders are skipped (SESSION_PASSED), orders still open are
     * reconciled once against the broker list of their order date, and whatever cannot be confirmed
     * becomes UNKNOWN (UNRESOLVED_AFTER_SESSION) for an operator. Nothing is sent or cancelled.
     */
    void cleanupPastSessions(String userId, List<StrategyBatch> userBatches, OffsetDateTime now) {
        Map<StrategyBatch, List<StrategyBatchOrder>> ordersByBatch = new LinkedHashMap<>();
        Map<LocalDate, List<StrategyBatchOrder>> trackedByDate = new LinkedHashMap<>();
        Map<StrategyBatchOrder, StrategyBatch> owner = new HashMap<>();
        for (StrategyBatch batch : userBatches) {
            List<StrategyBatchOrder> orders = batchOrders.findByBatchIdOrderBySeqAsc(batch.getId());
            ordersByBatch.put(batch, orders);
            for (StrategyBatchOrder order : orders) {
                if ("PENDING".equals(order.getStatus())) {
                    order.setStatus("SKIPPED");
                    order.setRejectReason("SESSION_PASSED");
                    save(order, now);
                } else if (tracked(order)) {
                    LocalDate day = order.getOrderDate() == null ? batch.getSessionDate() : order.getOrderDate();
                    trackedByDate.computeIfAbsent(day, ignored -> new ArrayList<>()).add(order);
                    owner.put(order, batch);
                } else if (TRACKED.contains(order.getStatus())) {
                    unresolved(order, now); // open without a broker order number
                }
            }
        }
        if (!trackedByDate.isEmpty()) {
            try {
                User user = snapshots.paperUser(userId);
                String binding = snapshots.binding(user);
                String token = kis.fetchAccessToken(userId, user.getSecret());
                if (token == null || token.isBlank()) throw new IllegalStateException("KIS_TOKEN_UNAVAILABLE");
                for (Map.Entry<LocalDate, List<StrategyBatchOrder>> entry : trackedByDate.entrySet()) {
                    List<Map<String, Object>> rows = kis.paperOrders(userId, user.getSecret(), token, entry.getKey(), entry.getKey());
                    for (StrategyBatchOrder order : entry.getValue()) {
                        if (!binding.equals(owner.get(order).getAccountBinding())) unresolved(order, now);
                        else reconcileOrder(owner.get(order), order, rows, user, token, now, true);
                    }
                }
            } catch (RuntimeException ex) {
                log.warn("[StrategyBatchReconciler] past orders of {} could not be read from KIS: {}", userId, ex.getMessage());
            }
            trackedByDate.values().forEach(list -> list.stream().filter(order -> TRACKED.contains(order.getStatus()))
                    .forEach(order -> unresolved(order, now)));
        }
        for (Map.Entry<StrategyBatch, List<StrategyBatchOrder>> entry : ordersByBatch.entrySet()) {
            batchService.finalizeStatus(entry.getKey(), entry.getValue(), now);
        }
    }

    private void unresolved(StrategyBatchOrder order, OffsetDateTime now) {
        order.setStatus("UNKNOWN");
        order.setRejectReason("UNRESOLVED_AFTER_SESSION");
        save(order, now);
    }

    private void reconcileOrder(StrategyBatch batch, StrategyBatchOrder order, List<Map<String, Object>> rows, User user,
            String token, OffsetDateTime now, boolean pastSession) {
        OffsetDateTime close = sessions.sessionCloseAt(batch.getSessionDate());
        boolean settled = pastSession || close == null || !now.isBefore(close.plus(StrategySessionClock.CLOSE_SETTLE));
        String day = order.getOrderDate() == null ? null : order.getOrderDate().format(DateTimeFormatter.BASIC_ISO_DATE);
        List<Map<String, Object>> matches = rows.stream()
                .filter(row -> sameOrderNumber(order.getBrokerOrderId(), row.get("odno")) && String.valueOf(row.get("ord_dt")).equals(day))
                .toList();
        if (matches.size() != 1) {
            if (settled) {
                order.setStatus("UNKNOWN");
                order.setRejectReason(pastSession ? "UNRESOLVED_AFTER_SESSION" : "BROKER_ORDER_NOT_FOUND");
                save(order, now);
            }
            return;
        }
        Map<String, Object> row = matches.get(0);
        String expectedSide = order.isBuy() ? "02" : "01";
        if (!order.getStockCode().equals(String.valueOf(row.get("pdno")))
                || !expectedSide.equals(String.valueOf(row.get("sll_buy_dvsn_cd")))
                || integer(row.get("ord_qty")) != order.getQuantity()) {
            order.setStatus("UNKNOWN");
            order.setRejectReason("BROKER_ORDER_IDENTITY_MISMATCH");
            save(order, now);
            return;
        }
        long filled = integer(row.get("tot_ccld_qty"));
        long remaining = integer(row.get("rmn_qty"));
        // Same fields and fallbacks as PaperTradeLifecycle.reconcileAccount.
        long cancelledQuantity = integer(row.containsKey("cncl_cfrm_qty") ? row.get("cncl_cfrm_qty") : row.get("cnc_cfrm_qty"));
        long rejectedQuantity = integer(row.get("rjct_qty"));
        boolean closed = remaining == 0 && ("Y".equals(String.valueOf(row.get("cncl_yn")))
                || filled + cancelledQuantity + rejectedQuantity == order.getQuantity());
        order.setFilledQuantity(Math.max(order.getFilledQuantity(), filled));
        if (order.getFilledQuantity() > 0) order.setAverageFillPrice(new BigDecimal(String.valueOf(decimal(row.get("avg_prvs")))));
        String organization = row.get("ord_gno_brno") == null ? "" : String.valueOf(row.get("ord_gno_brno")).trim();
        if (!organization.isBlank()) order.setOrderOrg(organization);
        if (order.getFilledQuantity() >= order.getQuantity()) {
            order.setStatus("FILLED");
            order.setRejectReason(null);
        } else if (closed) {
            boolean brokerRejected = order.getFilledQuantity() == 0 && rejectedQuantity == order.getQuantity();
            order.setStatus(brokerRejected ? "REJECTED" : "CANCELLED");
            order.setRejectReason(brokerRejected ? "KIS_ORDER_REJECTED"
                    : order.getCancelRequestedAt() != null ? "CANCELLED_AFTER_OPEN" : "CANCELLED_AT_BROKER");
        } else if (settled) {
            // Day orders do not survive the session; the broker list may lag the expiry.
            order.setStatus("CANCELLED");
            order.setRejectReason("UNFILLED_AT_SESSION_CLOSE");
        } else {
            order.setStatus(order.getFilledQuantity() > 0 ? "PARTIALLY_FILLED" : "SUBMITTED");
            OffsetDateTime cancelAt = sessions.openCancelAt(batch.getSessionDate());
            if ("OPEN".equals(batch.getPhase()) && cancelAt != null && !now.isBefore(cancelAt)
                    && order.getCancelRequestedAt() == null && remaining > 0) {
                order.setCancelRequestedAt(now);
                save(order, now); // the cancel is counted even if the process stops during the call
                Map<String, Object> response;
                try {
                    response = kis.cancelPaperOrder(batch.getUserId(), user.getSecret(), token,
                            order.getBrokerOrderId(), order.getOrderOrg(), Math.toIntExact(remaining));
                } catch (IllegalArgumentException ex) {
                    // Raised before any HTTP request (missing broker organization, non-PAPER secret).
                    response = Map.of("success", false, "notSent", true);
                }
                if (Boolean.TRUE.equals(response.get("notSent"))) {
                    order.setRejectReason("CANCEL_NOT_SENT");
                } else if (!Boolean.TRUE.equals(response.get("success"))) {
                    Object raw = response.get("response");
                    boolean rejected = !Boolean.TRUE.equals(response.get("unknown")) && raw instanceof Map<?, ?> body
                            && body.get("rt_cd") != null && !"0".equals(String.valueOf(body.get("rt_cd")));
                    order.setRejectReason(rejected ? "KIS_CANCEL_REJECTED" : "CANCEL_CONFIRMATION_REQUIRED");
                } else {
                    order.setRejectReason("CANCEL_REQUESTED");
                }
            }
        }
        save(order, now);
    }

    private static boolean tracked(StrategyBatchOrder order) {
        return TRACKED.contains(order.getStatus()) && order.getBrokerOrderId() != null && !order.getBrokerOrderId().isBlank();
    }

    private void save(StrategyBatchOrder order, OffsetDateTime now) {
        order.setUpdatedAt(now);
        batchOrders.saveAndFlush(order);
    }

    private static boolean sameOrderNumber(String stored, Object listed) {
        if (stored == null || listed == null) return false;
        try {
            return new BigInteger(stored.trim()).equals(new BigInteger(String.valueOf(listed).trim()));
        } catch (NumberFormatException ex) {
            return stored.trim().equals(String.valueOf(listed).trim());
        }
    }

    private static double decimal(Object raw) {
        if (raw == null) throw new IllegalStateException("BROKER_NUMBER_MISSING");
        double value = Double.parseDouble(String.valueOf(raw).trim());
        if (!Double.isFinite(value)) throw new IllegalStateException("BROKER_NUMBER_INVALID");
        return value;
    }

    private static long integer(Object raw) {
        double value = decimal(raw);
        if (value < 0 || value > Long.MAX_VALUE || value != Math.rint(value)) throw new IllegalStateException("BROKER_QUANTITY_INVALID");
        return (long) value;
    }
}
