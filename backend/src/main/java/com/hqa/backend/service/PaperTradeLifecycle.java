package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.time.Clock;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;

@Service
public class PaperTradeLifecycle {
    private static final Logger log = LoggerFactory.getLogger(PaperTradeLifecycle.class);
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private final TradeSignalRepository signals;
    private final TradeSignalExecutionRepository executions;
    private final PaperAccountSnapshotService accounts;
    private final PaperTradeStore store;
    private final KisClient kis;
    private final ObjectMapper mapper;
    private final Clock clock;
    private final KrxSessionCalendar sessions;

    @Autowired
    public PaperTradeLifecycle(TradeSignalRepository signals, TradeSignalExecutionRepository executions,
            PaperAccountSnapshotService accounts, PaperTradeStore store, KisClient kis, ObjectMapper mapper,
            KrxSessionCalendar sessions) {
        this(signals, executions, accounts, store, kis, mapper, Clock.system(ZoneId.of("Asia/Seoul")), sessions);
    }

    public PaperTradeLifecycle(TradeSignalRepository signals, TradeSignalExecutionRepository executions,
            PaperAccountSnapshotService accounts, PaperTradeStore store, KisClient kis, ObjectMapper mapper, Clock clock) {
        this(signals, executions, accounts, store, kis, mapper, clock, KrxSessionCalendar.regular());
    }

    public PaperTradeLifecycle(TradeSignalRepository signals, TradeSignalExecutionRepository executions,
            PaperAccountSnapshotService accounts, PaperTradeStore store, KisClient kis, ObjectMapper mapper, Clock clock,
            KrxSessionCalendar sessions) {
        this.sessions = sessions;
        this.signals = signals;
        this.executions = executions;
        this.accounts = accounts;
        this.store = store;
        this.kis = kis;
        this.mapper = mapper;
        this.clock = clock;
    }

    public TradeSignal save(InternalTradeSignalRequest request) {
        accounts.paperUser(request.userId());
        var duplicate = request.idempotencyKey() == null ? Optional.<TradeSignal>empty()
                : store.receipt(request.idempotencyKey(), request.userId());
        if (duplicate.isPresent()) {
            if (!request.userId().equals(duplicate.get().getUserId())) throw new IllegalArgumentException("IDEMPOTENCY_CONFLICT");
            return duplicate.get();
        }
        Map<String, Object> account = account(request.userId());
        return store.save(request, account, now());
    }

    public Optional<TradeSignal> trigger(String signalId, Map<String, Object> request) {
        Optional<TradeSignal> found = signals.findById(signalId);
        if (found.isEmpty()) return found;
        TradeSignal signal = found.get();
        try {
            User user = accounts.paperUser(signal.getUserId());
            if (!"PAPER".equals(signal.getAccountMode())) throw new IllegalStateException("PAPER_ACCOUNT_REQUIRED");
            if (!accounts.binding(user).equals(signal.getAccountBinding())) throw new IllegalStateException("ACCOUNT_BINDING_CHANGED");
            TradeConditions.TriggerType type = TradeConditions.TriggerType.valueOf(String.valueOf(request.get("triggerType")));
            // Auto-trade is the entry switch (as in the account snapshot's entryEligible); turning it
            // off must not leave open positions without their stops and exits.
            if (type == TradeConditions.TriggerType.ENTRY && !user.isAutoTradeEnabled()) {
                throw new IllegalStateException("AUTO_TRADE_DISABLED");
            }
            Map<String, Object> payload = payload(signal);
            int version = request.get("planVersion") instanceof Number n ? integer(n) : -1;
            if (!TradeConditions.isV2(payload) && version < 0) version = signal.getPlanVersion();
            if (version != signal.getPlanVersion()) throw new IllegalArgumentException("STALE_PLAN_VERSION");
            String groupId = String.valueOf(request.get("groupId"));
            TradeConditions.Group group = TradeConditions.groups(payload, type).stream()
                    .filter(item -> item.id().equals(groupId)).findFirst().orElse(null);
            boolean scheduledExit = type == TradeConditions.TriggerType.EXIT && "planned-exit".equals(groupId)
                    && "OPEN".equals(signal.getStatus()) && signal.getPlannedExitAt() != null
                    && !signal.getPlannedExitAt().isAfter(now());
            if (group == null && !scheduledExit) throw new IllegalArgumentException("UNKNOWN_CONDITION_GROUP");
            if (!marketOpen()) throw new IllegalStateException("MARKET_CLOSED");
            Map<String, Object> account = account(signal.getUserId());
            String token = token(user);
            Long price = kis.inquireCurrentPrice(user.getUserId(), user.getSecret(), token, signal.getStockCode());
            if (price == null || price <= 0) throw new IllegalStateException("CURRENT_PRICE_UNAVAILABLE");
            Map<?, ?> holding = PaperTradeStore.holding(account, signal.getStockCode());
            Map<String, Object> snapshot = new LinkedHashMap<>();
            snapshot.put("current_price", price);
            snapshot.put("holding_quantity", holding == null ? 0L : holding.get("quantity"));
            double averagePrice = holding == null ? 0 : TradeConditions.number(holding.get("avgPrice"));
            snapshot.put("pnl_rate", averagePrice > 0 ? (price / averagePrice - 1) * 100 : null);
            // Whole seconds, like the monitor's snapshot, so "==" and "<=" boundaries agree.
            snapshot.put("market_time", now().toLocalTime().truncatedTo(java.time.temporal.ChronoUnit.SECONDS).toString());
            if (!scheduledExit && !TradeConditions.matches(group, snapshot)) throw new IllegalStateException("CONDITION_NO_LONGER_MATCHES");
            if (type == TradeConditions.TriggerType.ENTRY && TradeConditions.groups(payload, TradeConditions.TriggerType.INVALIDATION)
                    .stream().anyMatch(invalidation -> TradeConditions.matches(invalidation, snapshot))) {
                throw new IllegalStateException("ENTRY_INVALIDATION_MATCHES");
            }
            List<TradeSignalExecution> pending = executions.findByUserIdAndStatusIn(user.getUserId(), PaperTradeStore.UNRESOLVED).stream()
                    .filter(item -> signalId.equals(item.getSignalId())).toList();
            if (type == TradeConditions.TriggerType.INVALIDATION && "WAITING_ENTRY".equals(signal.getStatus())) {
                if (!pending.isEmpty()) reconcileAccount(user.getUserId(), signalId);
                store.expireEntry(signalId, version, true, now());
                return signals.findById(signalId);
            }
            if (!pending.isEmpty()) {
                // A protective trigger cancels a still-working entry BUY. A working SELL is kept when it
                // is this same trigger (the monitor re-sends a condition that stays true every poll, and
                // cancelling it would leave the position unprotected between polls) or when this trigger
                // is only a reduction. Otherwise it is cancelled so this exit can follow: a partial
                // reduction, or another group's limit order the falling price has left, must not hold
                // back a full stop until it expires.
                String base = signalId + ":" + version + ":" + type.name() + ":" + groupId + ":";
                boolean keepWorkingSell = pending.stream().anyMatch(item -> "SELL".equals(item.getOrderSide())
                        && (type == TradeConditions.TriggerType.REDUCE
                            || (item.getTriggerKey() != null && item.getTriggerKey().startsWith(base))));
                if (type != TradeConditions.TriggerType.ENTRY) reconcileAccount(user.getUserId(), keepWorkingSell ? null : signalId);
                throw new IllegalStateException("ORDER_RECONCILIATION_REQUIRED");
            }
            long powerCash = 0;
            long powerQuantity = 0;
            if (type == TradeConditions.TriggerType.ENTRY) {
                if (signal.getSignalPrice() == null || signal.getSignalPrice() <= 0) throw new IllegalStateException("SIGNAL_PRICE_REQUIRED");
                if (Math.abs((price - signal.getSignalPrice()) * 100.0 / signal.getSignalPrice()) > 3.0) {
                    throw new IllegalStateException("PRICE_DRIFT_EXCEEDED");
                }
                Map<String, Object> power = kis.paperPurchasingPower(user.getUserId(), user.getSecret(), token, signal.getStockCode(), price);
                powerCash = PaperAccountSnapshotService.nonnegativeLong(power.get("cash"));
                powerQuantity = PaperAccountSnapshotService.nonnegativeLong(power.get("quantity"));
            }
            TradeSignalExecution intent = store.claim(signalId, version, type, groupId, account,
                    price, powerCash, powerQuantity, group == null ? null : group.reduceFraction(), now());
            Map<String, Object> response;
            try {
                response = kis.paperOrder(user.getUserId(), user.getSecret(), token, signal.getStockCode(),
                        intent.getSubmittedQuantity(), price, intent.getOrderSide());
            } catch (RuntimeException ex) {
                response = Map.of("success", false, "unknown", true, "error", ex.getClass().getSimpleName());
            }
            store.acknowledge(intent.getId(), response);
        } catch (IllegalArgumentException | IllegalStateException ex) {
            store.block(signalId, ex.getMessage());
        }
        return signals.findById(signalId);
    }

    public boolean hasReceipt(String key) { return store.hasReceipt(key); }

    public Map<String, Object> triggerResponse(String signalId, Map<String, Object> request) {
        TradeSignal signal = trigger(signalId, request).orElseThrow(() -> new IllegalArgumentException("SIGNAL_NOT_FOUND"));
        String version = String.valueOf(request.getOrDefault("planVersion", signal.getPlanVersion()));
        String base = signalId + ":" + version + ":" + request.get("triggerType") + ":" + request.get("groupId") + ":";
        TradeSignalExecution execution = executions.findBySignalId(signalId).stream()
                .filter(item -> item.getTriggerKey() != null && item.getTriggerKey().startsWith(base))
                .max(java.util.Comparator.comparing(TradeSignalExecution::getSubmittedAt)).orElse(null);
        String status = execution == null ? null : execution.getStatus();
        String reason = signal.getRejectReason();
        boolean deduplicated = execution != null && Set.of("ORDER_RECONCILIATION_REQUIRED", "TRIGGER_ALREADY_CONSUMED")
                .contains(reason == null ? "" : reason);
        boolean invalidated = "INVALIDATION".equals(request.get("triggerType")) && "EXPIRED".equals(signal.getStatus())
                && "ENTRY_EXPIRED_OR_INVALIDATED".equals(reason);
        boolean accepted = invalidated || (execution != null
                && Set.of("ORDER_SUBMITTED", "PARTIALLY_FILLED", "FILLED").contains(status)
                && (reason == null || deduplicated));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("signalId", signalId);
        result.put("status", signal.getStatus());
        result.put("accepted", accepted);
        result.put("deduplicated", deduplicated);
        result.put("rejectReason", accepted ? null : reason);
        result.put("executionStatus", status);
        return result;
    }

    public Map<String, Object> active(int page, int size) {
        if (page < 0 || size < 1 || size > 500) throw new IllegalArgumentException("Invalid active signal page");
        var result = signals.findByStatusIn(PaperTradeStore.ACTIVE,
                PageRequest.of(page, size, Sort.by("createdAt").ascending().and(Sort.by("id").ascending())));
        // A plan's unresolved orders hold back every trigger until they are reconciled; an
        // UNKNOWN order (no broker ID) needs an operator, so the monitor reports it.
        Map<String, List<Map<String, Object>>> unresolved = new HashMap<>();
        for (TradeSignalExecution execution : executions.findByStatusInOrderBySubmittedAtAsc(PaperTradeStore.UNRESOLVED)) {
            Map<String, Object> order = new LinkedHashMap<>();
            order.put("status", execution.getStatus());
            order.put("orderSide", execution.getOrderSide());
            order.put("brokerOrderKnown", execution.getOrderId() != null && !execution.getOrderId().isBlank());
            order.put("submittedAt", execution.getSubmittedAt());
            unresolved.computeIfAbsent(execution.getSignalId(), ignored -> new ArrayList<>()).add(order);
        }
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("signals", result.getContent().stream()
                .map(signal -> monitorRow(signal, unresolved.getOrDefault(signal.getId(), List.of()))).toList());
        response.put("hasMore", result.hasNext());
        response.put("nextPage", result.hasNext() ? page + 1 : null);
        return response;
    }

    public List<Map<String, Object>> allActive() {
        List<Map<String, Object>> result = new ArrayList<>();
        int page = 0;
        while (true) {
            Map<String, Object> batch = active(page++, 500);
            @SuppressWarnings("unchecked") List<Map<String, Object>> rows = (List<Map<String, Object>>) batch.get("signals");
            result.addAll(rows);
            if (!Boolean.TRUE.equals(batch.get("hasMore"))) return result;
        }
    }

    public void expireEntries() {
        for (Map<String, Object> row : allActive()) {
            TradeSignal signal = signals.findById(String.valueOf(row.get("signalId"))).orElseThrow();
            if ("WAITING_ENTRY".equals(signal.getStatus()) && signal.getEntryValidUntil() != null
                    && !signal.getEntryValidUntil().isAfter(now())) store.expireEntry(signal.getId(), signal.getPlanVersion(), false, now());
        }
    }

    /** UNKNOWN orders (no broker ID) with what an operator needs to find them in the broker's order history. */
    public List<Map<String, Object>> ordersAwaitingOperator() {
        List<Map<String, Object>> rows = new ArrayList<>();
        for (TradeSignalExecution execution : executions.findByStatusInOrderBySubmittedAtAsc(List.of("UNKNOWN"))) {
            if (execution.getOrderId() != null && !execution.getOrderId().isBlank()) continue;
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("executionId", execution.getId());
            row.put("signalId", execution.getSignalId());
            row.put("userId", execution.getUserId());
            row.put("stockCode", execution.getStockCode());
            row.put("triggerType", execution.getTriggerType());
            row.put("orderSide", execution.getOrderSide());
            row.put("quantity", execution.getSubmittedQuantity());
            row.put("orderPrice", execution.getOrderPrice());
            row.put("submittedAt", execution.getSubmittedAt());
            row.put("rejectReason", execution.getRejectReason());
            rows.add(row);
        }
        return rows;
    }

    /**
     * Records an operator's finding for an UNKNOWN order, checked against the broker's own order
     * history for the submission date. brokerOrderId adopts that order if it has the same stock,
     * side and quantity, and reconciliation then resumes. notSubmitted is refused while the broker
     * lists an order from that time on that could be this submission and is tied to no other one,
     * so a real order is never treated as unsent (and later sent again).
     */
    public Map<String, Object> resolveUnknownOrder(String executionId, String brokerOrderId, boolean notSubmitted, String note) {
        if (note == null || note.isBlank() || note.length() > 500) {
            throw new IllegalArgumentException("A note of at most 500 characters is required");
        }
        boolean adopt = brokerOrderId != null && !brokerOrderId.isBlank();
        if (adopt == notSubmitted) throw new IllegalArgumentException("Give either brokerOrderId or notSubmitted");
        if (adopt && !brokerOrderId.matches("[0-9]{1,20}")) throw new IllegalArgumentException("Invalid broker order ID");
        TradeSignalExecution execution = executions.findById(executionId)
                .orElseThrow(() -> new IllegalArgumentException("EXECUTION_NOT_FOUND"));
        PaperTradeStore.requireAwaitingOperator(execution);
        User user = accounts.paperUser(execution.getUserId());
        if (!accounts.binding(user).equals(execution.getAccountBinding())) throw new IllegalStateException("ACCOUNT_BINDING_CHANGED");
        var submitted = execution.getSubmittedAt().atZoneSameInstant(KST);
        LocalDate day = submitted.toLocalDate();
        String side = "BUY".equals(execution.getOrderSide()) ? "02" : "01";
        List<Map<String, Object>> sameOrder = kis.paperOrders(user.getUserId(), user.getSecret(), token(user), day, day).stream()
                .filter(row -> day.format(java.time.format.DateTimeFormatter.BASIC_ISO_DATE).equals(String.valueOf(row.get("ord_dt")))
                        && execution.getStockCode().equals(String.valueOf(row.get("pdno")))
                        && side.equals(String.valueOf(row.get("sll_buy_dvsn_cd")))
                        && integer(row.get("ord_qty")) == execution.getSubmittedQuantity())
                .toList();
        if (adopt) {
            List<Map<String, Object>> match = sameOrder.stream().filter(row -> sameOrderNumber(brokerOrderId, row.get("odno"))).toList();
            if (match.size() != 1) throw new IllegalStateException("BROKER_ORDER_NOT_FOUND_FOR_THIS_SUBMISSION");
            String orderId = String.valueOf(match.get(0).get("odno"));
            if (associated(execution, orderId, day)) throw new IllegalStateException("BROKER_ORDER_ALREADY_ASSOCIATED");
            store.adoptBrokerOrder(executionId, orderId, String.valueOf(match.get(0).get("ord_gno_brno")), note.trim(), now());
        } else {
            LocalTime from = submitted.toLocalTime().minusMinutes(1);
            List<String> possible = sameOrder.stream()
                    .filter(row -> { LocalTime at = orderTime(row.get("ord_tmd")); return at == null || !at.isBefore(from); })
                    .map(row -> String.valueOf(row.get("odno")))
                    .filter(orderId -> !associated(execution, orderId, day)).toList();
            if (!possible.isEmpty()) throw new IllegalStateException("BROKER_LISTS_POSSIBLE_ORDER:" + String.join(",", possible));
            store.confirmNotSubmitted(executionId, note.trim(), now());
        }
        TradeSignalExecution updated = executions.findById(executionId).orElseThrow();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("executionId", executionId);
        result.put("signalId", updated.getSignalId());
        result.put("status", updated.getStatus());
        result.put("orderId", updated.getOrderId());
        return result;
    }

    private boolean associated(TradeSignalExecution execution, String orderId, LocalDate day) {
        return executions.findByUserIdAndOrderId(execution.getUserId(), orderId).stream()
                .anyMatch(other -> !other.getId().equals(execution.getId())
                        && other.getSubmittedAt().atZoneSameInstant(KST).toLocalDate().equals(day));
    }

    private static boolean sameOrderNumber(String typed, Object listed) {
        try { return new java.math.BigInteger(typed).equals(new java.math.BigInteger(String.valueOf(listed).trim())); }
        catch (NumberFormatException ex) { return false; }
    }

    private static LocalTime orderTime(Object raw) {
        try { return LocalTime.parse(String.valueOf(raw), java.time.format.DateTimeFormatter.ofPattern("HHmmss")); }
        catch (java.time.format.DateTimeParseException ex) { return null; }
    }

    public void reconcilePendingOrders() {
        List<TradeSignalExecution> pending = executions.findByStatusInOrderBySubmittedAtAsc(PaperTradeStore.UNRESOLVED);
        for (String userId : pending.stream().map(TradeSignalExecution::getUserId).distinct().toList()) {
            try { reconcileAccount(userId, null); }
            catch (RuntimeException ex) { log.error("PAPER order reconciliation failed for user {}: {}", userId, ex.getMessage()); }
        }
    }

    public void reconcileAccount(String userId, String cancelSignalId) {
        User user = accounts.paperUser(userId);
        List<TradeSignalExecution> pending = executions.findByUserIdAndStatusIn(userId, PaperTradeStore.UNRESOLVED);
        if (pending.isEmpty()) return;
        OffsetDateTime now = now();
        for (TradeSignalExecution execution : pending) {
            if ("INTENT".equals(execution.getStatus()) && execution.getSubmittedAt().isBefore(now.minusSeconds(30))) {
                store.markUnknown(execution.getId());
            }
        }
        List<TradeSignalExecution> known = pending.stream().filter(item -> item.getOrderId() != null && !item.getOrderId().isBlank()).toList();
        if (known.isEmpty()) return; // No broker identity means no automated re-submission or guessed association.
        LocalDate from = known.stream().map(item -> item.getSubmittedAt().toLocalDate()).min(LocalDate::compareTo).orElseThrow();
        String token = token(user);
        List<Map<String, Object>> orders = kis.paperOrders(userId, user.getSecret(), token, from, now.toLocalDate());
        for (TradeSignalExecution execution : known) {
            if (!accounts.binding(user).equals(execution.getAccountBinding())) {
                store.block(execution.getSignalId(), "ACCOUNT_BINDING_CHANGED");
                continue;
            }
            List<Map<String, Object>> matches = orders.stream().filter(row -> execution.getOrderId().equals(String.valueOf(row.get("odno")))
                    && execution.getSubmittedAt().toLocalDate().format(java.time.format.DateTimeFormatter.BASIC_ISO_DATE)
                    .equals(String.valueOf(row.get("ord_dt")))).toList();
            if (matches.size() != 1) {
                store.block(execution.getSignalId(), "BROKER_ORDER_ID_NOT_UNIQUE_OR_MISSING");
                continue;
            }
            Map<String, Object> row = matches.get(0);
            String expectedSide = "BUY".equals(execution.getOrderSide()) ? "02" : "01";
            if (!execution.getStockCode().equals(String.valueOf(row.get("pdno")))
                    || !expectedSide.equals(String.valueOf(row.get("sll_buy_dvsn_cd")))
                    || integer(row.get("ord_qty")) != execution.getSubmittedQuantity()) {
                throw new IllegalStateException("BROKER_ORDER_IDENTITY_MISMATCH");
            }
            int filled = integer(row.get("tot_ccld_qty"));
            int remaining = integer(row.get("rmn_qty"));
            int cancelledQuantity = integer(row.get("cnc_cfrm_qty"));
            int rejectedQuantity = integer(row.get("rjct_qty"));
            boolean cancelled = remaining == 0 && ("Y".equals(String.valueOf(row.get("cncl_yn")))
                    || filled + cancelledQuantity + rejectedQuantity == execution.getSubmittedQuantity());
            long average = Math.round(decimal(row.get("avg_prvs")));
            String organization = String.valueOf(row.get("ord_gno_brno"));
            store.observeFill(execution.getId(), filled, average, remaining, cancelled, organization, now);
            TradeSignal plan = signals.findById(execution.getSignalId()).orElseThrow();
            boolean entryExpired = "BUY".equals(execution.getOrderSide()) && plan.getEntryValidUntil() != null
                    && !plan.getEntryValidUntil().isAfter(now);
            if (remaining > 0 && (execution.getSignalId().equals(cancelSignalId)
                    || entryExpired || (execution.getOrderExpiresAt() != null && !execution.getOrderExpiresAt().isAfter(now)))) {
                if (store.markCancelRequested(execution.getId())) {
                    Map<String, Object> response = kis.cancelPaperOrder(userId, user.getSecret(), token,
                            execution.getOrderId(), organization, remaining);
                    store.acknowledgeCancellation(execution.getId(), response);
                }
            }
        }
    }

    private Map<String, Object> monitorRow(TradeSignal signal, List<Map<String, Object>> unresolvedOrders) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("signalId", signal.getId());
        row.put("userId", signal.getUserId());
        row.put("accountMode", signal.getAccountMode());
        row.put("status", signal.getStatus());
        row.put("stockCode", signal.getStockCode());
        row.put("stockName", signal.getStockName());
        row.put("action", signal.getAction());
        row.put("signalPrice", signal.getSignalPrice());
        row.put("planVersion", signal.getPlanVersion());
        row.put("analysisAsOf", signal.getAnalysisAsOf());
        row.put("entryValidUntil", signal.getEntryValidUntil());
        row.put("plannedExitAt", signal.getPlannedExitAt());
        row.put("managedQuantity", signal.getManagedQuantity());
        // One unreadable stored plan (legacy NULL or malformed conditions) must not fail the page
        // for every account; it is listed without conditions so the monitor reports it uncovered.
        Map<String, Object> conditions;
        String reason = signal.getRejectReason();
        try {
            conditions = payload(signal);
        } catch (IllegalArgumentException ex) {
            conditions = null;
            reason = "INVALID_STORED_CONDITIONS";
        }
        row.put("conditionPayload", conditions);
        row.put("rejectReason", reason);
        row.put("unresolvedOrders", unresolvedOrders);
        return row;
    }
    private Map<String, Object> account(String userId) {
        Map<String, Object> result = accounts.snapshot(userId);
        if (!Boolean.TRUE.equals(result.get("success"))) throw new IllegalStateException(String.valueOf(result.get("error")));
        OffsetDateTime capturedAt = OffsetDateTime.parse(String.valueOf(result.get("capturedAt")));
        long age = Duration.between(capturedAt, now()).toSeconds();
        if (age < 0 || age > 20) throw new IllegalStateException("ACCOUNT_SNAPSHOT_STALE");
        return result;
    }
    private String token(User user) {
        String token = kis.fetchAccessToken(user.getUserId(), user.getSecret());
        if (token == null || token.isBlank()) throw new IllegalStateException("KIS_TOKEN_UNAVAILABLE");
        return token;
    }
    private Map<String, Object> payload(TradeSignal signal) {
        try { return mapper.readValue(signal.getConditionPayload(), new TypeReference<>() { }); }
        catch (java.io.IOException ex) { throw new IllegalArgumentException("INVALID_STORED_CONDITIONS", ex); }
    }
    private OffsetDateTime now() { return OffsetDateTime.now(clock); }
    private boolean marketOpen() {
        return sessions.isOpen(now());
    }
    private static double decimal(Object raw) {
        if (raw == null) throw new IllegalStateException("BROKER_NUMBER_MISSING");
        double value = Double.parseDouble(String.valueOf(raw));
        if (!Double.isFinite(value)) throw new IllegalStateException("BROKER_NUMBER_INVALID");
        return value;
    }
    private static int integer(Object raw) {
        double value = decimal(raw);
        if (value < 0 || value > Integer.MAX_VALUE || value != Math.rint(value)) throw new IllegalStateException("BROKER_QUANTITY_INVALID");
        return (int) value;
    }
}
