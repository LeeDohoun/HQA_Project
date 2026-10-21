package com.hqa.backend.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.StrategyProperties;
import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import com.hqa.backend.entity.StrategyBatchSizing;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyBatchRepository;
import com.hqa.backend.repository.StrategyBatchSizingRepository;
import com.hqa.backend.util.StockCodes;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Accepts strategy rebalancing batches after the backend's own checks. Acceptance stores the batch as
 * QUEUED; only the executor sends orders, and only inside the phase window with orders enabled.
 */
@Service
public class StrategyBatchService {
    public static final String HEX64 = "[0-9a-fA-F]{64}";
    public static final Set<String> PHASES = Set.of("OPEN", "CLOSE", "CONT_SELL", "CONT_BUY");
    public static final double DEFAULT_COST_RESERVE_RATE = 0.003;
    public static final double MAX_COST_RESERVE_RATE = 0.05;
    /** Batch states before the executor finished sending. */
    public static final Set<String> UNSENT_BATCH_STATUSES = Set.of("QUEUED", "SUBMITTING");
    /** Order states with quantity that may still fill at the broker. */
    public static final Set<String> OPEN_ORDER_STATUSES = Set.of("PENDING", "SUBMITTED", "PARTIALLY_FILLED");
    private final StrategyAccountRepository accounts;
    private final StrategyBatchRepository batches;
    private final StrategyBatchOrderRepository batchOrders;
    private final PaperAccountSnapshotService snapshots;
    private final PaperAccountGuard accountGuard;
    private final StrategyRiskGuard risk;
    private final StrategySessionClock sessions;
    private final StrategyProperties properties;
    private final ObjectMapper mapper;
    private final TransactionTemplate transactions;
    private final StrategyBatchSizingRepository sizings;

    public StrategyBatchService(StrategyAccountRepository accounts, StrategyBatchRepository batches,
            StrategyBatchOrderRepository batchOrders, StrategyBatchSizingRepository sizings, PaperAccountSnapshotService snapshots,
            PaperAccountGuard accountGuard, StrategyRiskGuard risk, StrategySessionClock sessions, StrategyProperties properties,
            ObjectMapper mapper, PlatformTransactionManager transactionManager) {
        this.sizings = sizings;
        this.accounts = accounts;
        this.batches = batches;
        this.batchOrders = batchOrders;
        this.snapshots = snapshots;
        this.accountGuard = accountGuard;
        this.risk = risk;
        this.sessions = sessions;
        this.properties = properties;
        this.mapper = mapper;
        this.transactions = new TransactionTemplate(transactionManager);
    }

    public static String idempotencyKey(String strategyId, String decisionHash, String sessionDate, String phase, String userId) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(
                    String.join("|", strategyId, decisionHash, sessionDate, phase, userId).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException(ex);
        }
    }

    public Map<String, Object> create(StrategyBatchRequest request) {
        if (request == null) return rejected(List.of("INVALID_REQUEST"), 0, List.of());
        boolean dryRun = !Boolean.FALSE.equals(request.dryRun()); // a missing flag only validates
        int orderCount = request.orders() == null ? 0 : request.orders().size();
        if (blank(request.strategyId()) || blank(request.userId()) || request.decisionHash() == null
                || !request.decisionHash().matches(HEX64) || request.idempotencyKey() == null
                || !request.idempotencyKey().matches(HEX64) || request.decisionDate() == null || request.sessionDate() == null
                || request.phase() == null || !PHASES.contains(request.phase()) || request.orders() == null
                || !request.decisionDate().isBefore(request.sessionDate())
                || (request.costReserveRate() != null && (!"CONT_BUY".equals(request.phase())
                        || !Double.isFinite(request.costReserveRate()) || request.costReserveRate() < 0
                        || request.costReserveRate() >= MAX_COST_RESERVE_RATE))) {
            return rejected(List.of("INVALID_REQUEST"), orderCount, List.of());
        }
        String expectedKey = idempotencyKey(request.strategyId(), request.decisionHash(), request.sessionDate().toString(),
                request.phase(), request.userId());
        if (!expectedKey.equalsIgnoreCase(request.idempotencyKey())) {
            return rejected(List.of("IDEMPOTENCY_KEY_MISMATCH"), orderCount, List.of());
        }
        String key = request.idempotencyKey().toLowerCase();
        if (!dryRun) {
            Optional<StrategyBatch> stored = batches.findByIdempotencyKey(key);
            if (stored.isPresent()) return deduplicated(stored.get());
        }

        Optional<StrategyAccount> account = accounts.findById(request.userId());
        if (account.isEmpty()) return rejected(List.of("STRATEGY_ACCOUNT_NOT_FOUND"), orderCount, List.of());
        if (!account.get().getStrategyId().equals(request.strategyId())) {
            return rejected(List.of("STRATEGY_MISMATCH"), orderCount, List.of());
        }
        Set<String> reasons = new LinkedHashSet<>();
        if (!"paper".equals(account.get().getStage())) reasons.add("STRATEGY_STAGE_NOT_PAPER");
        if (!account.get().isEnabled()) reasons.add("STRATEGY_ACCOUNT_DISABLED");
        try {
            snapshots.paperUser(request.userId());
        } catch (IllegalArgumentException | IllegalStateException ex) {
            reasons.add("PAPER_ACCOUNT_REQUIRED");
            if (ex.getMessage() != null && !"PAPER_ACCOUNT_REQUIRED".equals(ex.getMessage())) reasons.add(ex.getMessage());
        }
        if (!reasons.isEmpty()) return rejected(List.copyOf(reasons), orderCount, List.of());

        OffsetDateTime now = sessions.now();
        if (!request.sessionDate().equals(now.toLocalDate())) reasons.add("SESSION_DATE_NOT_TODAY");
        if (!sessions.isSessionDay(request.sessionDate())) reasons.add("NOT_A_KRX_SESSION");
        else if (!sessions.calendarVerified(request.sessionDate())) reasons.add("CALENDAR_UNVERIFIED");
        if (!dryRun && !sessions.inPhaseWindow(request.sessionDate(), request.phase(), now)) reasons.add("OUTSIDE_PHASE_WINDOW");
        if (risk.isHalted(request.userId())) reasons.add("TRADING_HALTED");
        if (phaseTaken(request)) reasons.add("BATCH_ALREADY_EXISTS_FOR_PHASE");

        List<Map<String, Object>> rejectedOrders = new ArrayList<>();
        List<StrategyBatchRequest.Order> valid = validateOrders(request.orders(), reasons, rejectedOrders);
        String onlySide = "CONT_SELL".equals(request.phase()) ? "SELL" : "CONT_BUY".equals(request.phase()) ? "BUY" : null;
        if (onlySide != null) {
            for (StrategyBatchRequest.Order order : valid) {
                if (!onlySide.equals(order.side())) {
                    reasons.add("PHASE_SIDE_MISMATCH");
                    rejectedOrders.add(rejectedOrder(order.seq(), "PHASE_SIDE_MISMATCH"));
                }
            }
        }
        if (account.get().isTerminated() && valid.stream().anyMatch(order -> "BUY".equals(order.side()))) {
            // Terminated (close-to-close drawdown): only SELL batches, i.e. liquidation, are accepted.
            reasons.add("STRATEGY_TERMINATED");
            valid.stream().filter(order -> "BUY".equals(order.side()))
                    .forEach(order -> rejectedOrders.add(rejectedOrder(order.seq(), "STRATEGY_TERMINATED")));
        }

        if (reasons.isEmpty()) {
            StrategyRiskGuard.RiskState state = risk.evaluate(request.userId());
            if (state.halted()) reasons.add("TRADING_HALTED");
            if (!state.snapshotAvailable()) {
                reasons.add("ACCOUNT_SNAPSHOT_UNAVAILABLE");
            } else {
                double cap = state.equityKrw() * properties.getMaxOrderPctOfEquity() / 100.0;
                for (StrategyBatchRequest.Order order : valid) {
                    if ((double) order.quantity() * order.refPrice() > cap) {
                        reasons.add("ORDER_EXCEEDS_EQUITY_SHARE");
                        rejectedOrders.add(rejectedOrder(order.seq(), "ORDER_EXCEEDS_EQUITY_SHARE"));
                    }
                }
            }
            boolean hasBuys = valid.stream().anyMatch(order -> "BUY".equals(order.side()));
            if (hasBuys && !state.buyAllowed() && state.snapshotAvailable()) {
                // Daily loss and drawdown block buys only; a sells-only batch is still accepted.
                List<String> buyReasons = state.reasons().stream().filter(StrategyRiskGuard.BUY_BLOCK_REASONS::contains).toList();
                reasons.addAll(buyReasons.isEmpty() ? List.of(state.buyBlockReason()) : buyReasons);
                String reason = state.buyBlockReason();
                valid.stream().filter(order -> "BUY".equals(order.side()))
                        .forEach(order -> rejectedOrders.add(rejectedOrder(order.seq(), reason)));
            }
            if (state.ordersToday() + orderCount > properties.getMaxOrdersPerDay()) reasons.add("DAILY_ORDER_LIMIT_EXCEEDED");
            if (risk.cancelRatioExceeded(state.ordersToday(), state.cancelsToday())) reasons.add("CANCEL_RATIO_EXCEEDED");
        }
        if (!reasons.isEmpty()) return rejected(List.copyOf(reasons), orderCount, rejectedOrders);
        if (dryRun) return response(null, "VALIDATED", false, List.of(), orderCount, List.of());
        try {
            return transactions.execute(status -> persist(request, key, now));
        } catch (DataIntegrityViolationException ex) {
            // A concurrent request won the insert: the same key, or another batch for this phase.
            Optional<StrategyBatch> stored = batches.findByIdempotencyKey(key);
            if (stored.isPresent()) return deduplicated(stored.get());
            if (phaseTaken(request)) return rejected(List.of("BATCH_ALREADY_EXISTS_FOR_PHASE"), orderCount, List.of());
            throw ex;
        }
    }

    private Map<String, Object> persist(StrategyBatchRequest request, String key, OffsetDateTime now) {
        User user = accountGuard.lock(request.userId()); // serializes batches of one PAPER account
        Optional<StrategyBatch> stored = batches.findByIdempotencyKey(key);
        if (stored.isPresent()) return deduplicated(stored.get());
        List<String> reasons = new ArrayList<>();
        if (risk.isHalted(request.userId())) reasons.add("TRADING_HALTED");
        if (phaseTaken(request)) reasons.add("BATCH_ALREADY_EXISTS_FOR_PHASE");
        if (batchOrders.countOrdersForUserOn(request.userId(), request.sessionDate()) + request.orders().size()
                > properties.getMaxOrdersPerDay()) {
            reasons.add("DAILY_ORDER_LIMIT_EXCEEDED");
        }
        if (!reasons.isEmpty()) return rejected(reasons, request.orders().size(), List.of());
        StrategyBatch batch = new StrategyBatch();
        batch.setStrategyId(request.strategyId());
        batch.setUserId(request.userId());
        batch.setAccountBinding(accountGuard.binding(user));
        batch.setDecisionHash(request.decisionHash().toLowerCase());
        batch.setDecisionDate(request.decisionDate());
        batch.setSessionDate(request.sessionDate());
        batch.setPhase(request.phase());
        batch.setStatus("QUEUED");
        batch.setIdempotencyKey(key);
        batch.setExpectedNavKrw(request.expectedNavKrw());
        if ("CONT_BUY".equals(request.phase())) {
            batch.setCostReserveRate(request.costReserveRate() == null ? DEFAULT_COST_RESERVE_RATE : request.costReserveRate());
        }
        batch.setReasons(json(List.of()));
        batch.setCreatedAt(now);
        StrategyBatch saved = batches.saveAndFlush(batch);
        List<StrategyBatchOrder> rows = new ArrayList<>();
        for (StrategyBatchRequest.Order line : request.orders()) {
            StrategyBatchOrder order = new StrategyBatchOrder();
            order.setBatchId(saved.getId());
            order.setSeq(line.seq());
            order.setStockCode(line.stockCode());
            order.setSide(line.side());
            order.setQuantity(line.quantity());
            order.setPlannedQuantity(line.quantity());
            order.setRefPrice(line.refPrice());
            order.setAdv20Krw(line.adv20Krw());
            order.setOrderType(properties.getOrderType());
            order.setStatus("PENDING");
            order.setFilledQuantity(0);
            order.setUpdatedAt(now);
            rows.add(order);
        }
        batchOrders.saveAllAndFlush(rows);
        return response(saved.getId(), saved.getStatus(), false, List.of(), rows.size(), List.of());
    }

    /**
     * True while a CONT_BUY batch must wait for the same account's CONT_SELL batch of the session: until it
     * is terminal (sent, no order open at the broker), or its window has ended, every open remainder has a
     * cancel request and the reconciler has read the broker list after the last cancel request.
     */
    public boolean awaitingSells(StrategyBatch buyBatch, OffsetDateTime now) {
        if (!"CONT_BUY".equals(buyBatch.getPhase())) return false;
        Optional<StrategyBatch> found = batches.findFirstByUserIdAndSessionDateAndPhaseAndStatusNot(buyBatch.getUserId(),
                buyBatch.getSessionDate(), "CONT_SELL", "REJECTED");
        if (found.isEmpty()) return false;
        StrategyBatch sell = found.get();
        if (UNSENT_BATCH_STATUSES.contains(sell.getStatus())) {
            return !sessions.phaseWindowClosed(sell.getSessionDate(), "CONT_SELL", now);
        }
        List<StrategyBatchOrder> orders = batchOrders.findByBatchIdOrderBySeqAsc(sell.getId());
        List<StrategyBatchOrder> open = orders.stream().filter(order -> OPEN_ORDER_STATUSES.contains(order.getStatus())).toList();
        if (open.isEmpty()) return false;
        if (!sessions.phaseWindowClosed(sell.getSessionDate(), "CONT_SELL", now)) return true;
        if (open.stream().anyMatch(order -> order.getCancelRequestedAt() == null)) return true;
        OffsetDateTime lastCancel = open.stream().map(StrategyBatchOrder::getCancelRequestedAt).max(OffsetDateTime::compareTo).orElseThrow();
        return sell.getLastReconciledAt() == null || !sell.getLastReconciledAt().isAfter(lastCancel);
    }

    /** Another stored (not REJECTED) batch already covers this account, session and phase. */
    private boolean phaseTaken(StrategyBatchRequest request) {
        return batches.existsByUserIdAndSessionDateAndPhaseAndStatusNot(request.userId(), request.sessionDate(),
                request.phase(), "REJECTED");
    }

    private List<StrategyBatchRequest.Order> validateOrders(List<StrategyBatchRequest.Order> orders, Set<String> reasons,
            List<Map<String, Object>> rejectedOrders) {
        if (orders.isEmpty()) reasons.add("NO_ORDERS");
        if (orders.size() > properties.getMaxOrdersPerBatch()) {
            reasons.add("TOO_MANY_ORDERS");
            return List.of();
        }
        List<StrategyBatchRequest.Order> valid = new ArrayList<>();
        Set<Integer> seqs = new HashSet<>();
        Set<String> keys = new HashSet<>();
        int maxSellSeq = Integer.MIN_VALUE;
        int minBuySeq = Integer.MAX_VALUE;
        for (StrategyBatchRequest.Order order : orders) {
            if (order == null) {
                reasons.add("INVALID_ORDER");
                rejectedOrders.add(rejectedOrder(null, "INVALID_ORDER"));
                continue;
            }
            String problem = orderProblem(order);
            if (problem == null && !seqs.add(order.seq())) problem = "INVALID_ORDER";
            if (problem == null && !keys.add(order.stockCode() + "|" + order.side())) problem = "DUPLICATE_ORDER";
            if (problem != null) {
                reasons.add(problem);
                rejectedOrders.add(rejectedOrder(order.seq(), problem));
                continue;
            }
            if ("SELL".equals(order.side())) maxSellSeq = Math.max(maxSellSeq, order.seq());
            else minBuySeq = Math.min(minBuySeq, order.seq());
            valid.add(order);
        }
        if (maxSellSeq != Integer.MIN_VALUE && minBuySeq != Integer.MAX_VALUE && maxSellSeq > minBuySeq) {
            reasons.add("SELLS_MUST_PRECEDE_BUYS");
        }
        return valid;
    }

    private static String orderProblem(StrategyBatchRequest.Order order) {
        if (order.seq() == null || order.seq() <= 0 || !StockCodes.isValid(order.stockCode())
                || !("BUY".equals(order.side()) || "SELL".equals(order.side()))
                || order.quantity() == null || order.quantity() <= 0 || order.quantity() > Integer.MAX_VALUE
                || order.refPrice() == null || order.refPrice() <= 0
                || (order.adv20Krw() != null && (!Double.isFinite(order.adv20Krw()) || order.adv20Krw() < 0))) {
            return "INVALID_ORDER";
        }
        try {
            Math.multiplyExact(order.quantity(), order.refPrice());
        } catch (ArithmeticException ex) {
            return "INVALID_ORDER";
        }
        return null;
    }

    /** Closes a batch whose phase window has passed: unsent orders are skipped and the batch is SUBMITTED. */
    public void closeWindow(StrategyBatch batch, OffsetDateTime now) {
        List<StrategyBatchOrder> orders = batchOrders.findByBatchIdOrderBySeqAsc(batch.getId());
        List<StrategyBatchOrder> skipped = new ArrayList<>();
        for (StrategyBatchOrder order : orders) {
            if ("PENDING".equals(order.getStatus())) {
                order.setStatus("SKIPPED");
                order.setRejectReason("PHASE_WINDOW_CLOSED");
                order.setUpdatedAt(now);
                skipped.add(order);
            }
        }
        if (!skipped.isEmpty()) batchOrders.saveAllAndFlush(skipped);
        batch.setStatus("SUBMITTED");
        if (batch.getSubmittedAt() == null) batch.setSubmittedAt(now);
        batches.saveAndFlush(batch);
    }

    /** Final or interim batch status from its orders; HALTED stays HALTED. Returns true when it changed. */
    public boolean finalizeStatus(StrategyBatch batch, List<StrategyBatchOrder> orders, OffsetDateTime now) {
        return finalizeStatus(batch, orders, now, false);
    }

    /** As above; {@code save} also stores other field changes (e.g. lastReconciledAt) when the status did not change. */
    public boolean finalizeStatus(StrategyBatch batch, List<StrategyBatchOrder> orders, OffsetDateTime now, boolean save) {
        boolean open = orders.stream().anyMatch(order -> OPEN_ORDER_STATUSES.contains(order.getStatus()));
        boolean anyFilled = orders.stream().anyMatch(order -> order.getFilledQuantity() > 0);
        boolean allFilled = !orders.isEmpty() && orders.stream().allMatch(order -> "FILLED".equals(order.getStatus()));
        boolean anyUnknown = orders.stream().anyMatch(order -> "UNKNOWN".equals(order.getStatus()));
        String status;
        if ("HALTED".equals(batch.getStatus())) status = "HALTED";
        else if (open) status = anyFilled ? "PARTIAL" : "SUBMITTED";
        else if (allFilled) status = "COMPLETED";
        else if (anyFilled || anyUnknown) status = "PARTIAL";
        else status = "REJECTED";
        boolean changed = !status.equals(batch.getStatus());
        batch.setStatus(status);
        if (!open && batch.getCompletedAt() == null) {
            batch.setCompletedAt(now);
            changed = true;
        } else if (open && batch.getCompletedAt() != null) {
            batch.setCompletedAt(null); // an adopted UNKNOWN order reopened it
            changed = true;
        }
        if (changed || save) batches.saveAndFlush(batch);
        return changed;
    }

    public Map<String, Object> describe(UUID batchId) {
        StrategyBatch batch = batches.findById(batchId).orElseThrow(() -> new IllegalArgumentException("BATCH_NOT_FOUND"));
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("batchId", batch.getId().toString());
        view.put("strategyId", batch.getStrategyId());
        view.put("userId", batch.getUserId());
        view.put("phase", batch.getPhase());
        view.put("sessionDate", batch.getSessionDate().toString());
        view.put("decisionDate", batch.getDecisionDate().toString());
        view.put("decisionHash", batch.getDecisionHash());
        view.put("status", batch.getStatus());
        view.put("reasons", reasons(batch));
        view.put("createdAt", batch.getCreatedAt());
        view.put("submittedAt", batch.getSubmittedAt());
        view.put("completedAt", batch.getCompletedAt());
        view.put("costReserveRate", batch.getCostReserveRate());
        view.put("sizing", sizings.findById(batch.getId()).map(this::sizingView).orElse(null));
        view.put("orders", batchOrders.findByBatchIdOrderBySeqAsc(batch.getId()).stream().map(StrategyBatchService::orderView).toList());
        return view;
    }

    public static Map<String, Object> orderView(StrategyBatchOrder order) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("seq", order.getSeq());
        row.put("stockCode", order.getStockCode());
        row.put("side", order.getSide());
        row.put("quantity", order.getQuantity());
        row.put("plannedQuantity", order.getPlannedQuantity() == null ? order.getQuantity() : order.getPlannedQuantity());
        row.put("refPrice", order.getRefPrice());
        row.put("refPriceAtSubmit", order.getRefPriceAtSubmit());
        row.put("adv20Krw", order.getAdv20Krw());
        row.put("status", order.getStatus());
        row.put("brokerOrderId", order.getBrokerOrderId());
        row.put("filledQuantity", order.getFilledQuantity());
        row.put("averageFillPrice", order.getAverageFillPrice() == null ? null : order.getAverageFillPrice().doubleValue());
        row.put("rejectReason", order.getRejectReason());
        return row;
    }

    public Map<String, Object> sizingView(StrategyBatchSizing sizing) {
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("orderableCashKrw", sizing.getOrderableCashKrw());
        view.put("readAt", sizing.getReadAt());
        view.put("requiredKrw", sizing.getRequiredKrw());
        view.put("costReserveRate", sizing.getCostReserveRate());
        view.put("scaleFactor", sizing.getScaleFactor());
        view.put("scaled", sizing.isScaled());
        view.put("kisFields", readJson(sizing.getKisFields()));
        view.put("quantities", readJson(sizing.getQuantities()));
        return view;
    }

    private Object readJson(String text) {
        try {
            return mapper.readValue(text, Object.class);
        } catch (JsonProcessingException ex) {
            return text;
        }
    }

    private Map<String, Object> deduplicated(StrategyBatch batch) {
        int count = batchOrders.findByBatchIdOrderBySeqAsc(batch.getId()).size();
        return response(batch.getId(), batch.getStatus(), true, reasons(batch), count, List.of());
    }

    private List<String> reasons(StrategyBatch batch) {
        if (batch.getReasons() == null || batch.getReasons().isBlank()) return List.of();
        try {
            return mapper.readValue(batch.getReasons(), new TypeReference<List<String>>() { });
        } catch (JsonProcessingException ex) {
            return List.of(batch.getReasons());
        }
    }

    private static Map<String, Object> rejected(List<String> reasons, int orderCount, List<Map<String, Object>> rejectedOrders) {
        return response(null, "REJECTED", false, reasons, orderCount, rejectedOrders);
    }

    private static Map<String, Object> response(UUID batchId, String status, boolean deduplicated, List<String> reasons,
            int orderCount, List<Map<String, Object>> rejectedOrders) {
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("batchId", batchId == null ? null : batchId.toString());
        response.put("status", status);
        response.put("deduplicated", deduplicated);
        response.put("reasons", reasons);
        response.put("orderCount", orderCount);
        response.put("rejectedOrders", rejectedOrders);
        return response;
    }

    private static Map<String, Object> rejectedOrder(Integer seq, String reason) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("seq", seq);
        row.put("reason", reason);
        return row;
    }

    private String json(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException(ex);
        }
    }

    private static boolean blank(String value) {
        return value == null || value.isBlank();
    }
}
