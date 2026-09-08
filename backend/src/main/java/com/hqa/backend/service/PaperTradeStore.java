package com.hqa.backend.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.TradePlanReceipt;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.TradePlanReceiptRepository;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.time.OffsetDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Short transactions persist order intent before any broker mutation. */
@Service
public class PaperTradeStore {
    public static final List<String> ACTIVE = List.of("WAITING_ENTRY", "WAITING_EXIT", "OPEN", "ORDER_SUBMITTED", "PARTIALLY_FILLED");
    public static final List<String> UNRESOLVED = List.of("INTENT", "UNKNOWN", "ORDER_SUBMITTED", "PARTIALLY_FILLED", "CANCEL_REQUESTED");
    private final TradeSignalRepository signals;
    private final TradeSignalExecutionRepository executions;
    private final PaperAccountGuard accountGuard;
    private final ObjectMapper mapper;
    private final double quoteCapacity;
    private final TradePlanReceiptRepository receipts;

    public PaperTradeStore(TradeSignalRepository signals, TradeSignalExecutionRepository executions,
            PaperAccountGuard accountGuard, ObjectMapper mapper, TradePlanReceiptRepository receipts,
            @Value("${hqa.kis-paper-requests-per-second:1}") double requestsPerSecond,
            @Value("${hqa.paper-account-reserved-requests-per-second:0.5}") double reservedRequests) {
        if (!Double.isFinite(requestsPerSecond) || !Double.isFinite(reservedRequests)
                || requestsPerSecond <= reservedRequests || reservedRequests < 0) {
            throw new IllegalArgumentException("PAPER request capacity must exceed account request reservation");
        }
        this.signals = signals;
        this.executions = executions;
        this.accountGuard = accountGuard;
        this.mapper = mapper;
        this.receipts = receipts;
        this.quoteCapacity = Math.floor((requestsPerSecond - reservedRequests) * 20);
    }

    @Transactional
    public TradeSignal save(InternalTradeSignalRequest request, Map<String, Object> account, OffsetDateTime now) {
        User user = lock(request.userId());
        requireEnabledPaper(user);
        if (request.idempotencyKey() == null || request.idempotencyKey().isBlank()) {
            throw new IllegalArgumentException("idempotencyKey is required");
        }
        var duplicate = receipt(request.idempotencyKey(), request.userId());
        if (duplicate.isPresent()) {
            if (!request.userId().equals(duplicate.get().getUserId())) throw new IllegalArgumentException("IDEMPOTENCY_CONFLICT");
            return duplicate.get();
        }
        TradeConditions.validate(request.conditionPayload());
        boolean v2 = TradeConditions.isV2(request.conditionPayload());
        if (v2 && (request.analysisId() == null || request.analysisId().isBlank() || request.analysisAsOf() == null
                || request.analysisAsOf().isBefore(now.minusMinutes(15)) || request.analysisAsOf().isAfter(now.plusSeconds(5)))) {
            throw new IllegalArgumentException("A fresh analysisId and analysisAsOf are required for v2");
        }
        if (v2 && !"PAPER".equals(request.accountMode())) throw new IllegalArgumentException("PAPER_ACCOUNT_REQUIRED");
        if (!Set.of("BUY", "STRONG_BUY", "SELL", "STRONG_SELL", "REDUCE", "HOLD").contains(request.action())) {
            throw new IllegalArgumentException("Unsupported action");
        }
        if (request.stockCode() == null || !request.stockCode().matches("[0-9]{6}")) throw new IllegalArgumentException("Invalid stockCode");
        int version = request.planVersion() == null ? 1 : request.planVersion();
        if (version < 1) throw new IllegalArgumentException("planVersion must be positive");
        OffsetDateTime expiry = v2 ? request.entryValidUntil()
                : request.entryValidUntil() != null ? request.entryValidUntil() : request.expiresAt();
        if (v2 && expiry == null) throw new IllegalArgumentException("entryValidUntil is required for v2");
        if (v2 && (!expiry.isAfter(request.analysisAsOf()) || expiry.isAfter(request.analysisAsOf().plusMinutes(15)))) {
            throw new IllegalArgumentException("ENTRY_VALIDITY_MUST_BE_WITHIN_ANALYSIS_15_MINUTES");
        }
        if (v2 && (request.plannedExitAt() == null || !request.plannedExitAt().isAfter(expiry))) {
            throw new IllegalArgumentException("PLANNED_EXIT_MUST_FOLLOW_ENTRY_VALIDITY");
        }
        boolean buy = request.action().contains("BUY");
        if (buy && (expiry == null || !expiry.isAfter(now))) throw new IllegalArgumentException("Entry validity has expired");
        double target = targetPct(request);
        if (target < 0 || target > PaperAccountSnapshotService.MAX_POSITION_PCT || (buy && target <= 0)) {
            throw new IllegalArgumentException("targetPositionPct must be within the backend 20 percent cap");
        }
        if (buy && TradeConditions.groups(request.conditionPayload(), TradeConditions.TriggerType.ENTRY).isEmpty()) {
            throw new IllegalArgumentException("Entry conditions are required");
        }
        Double hardStop = TradeConditions.hardStop(request.conditionPayload());
        if (v2 && buy) {
            Object declared = request.tradePlanJson() == null ? null : request.tradePlanJson().get("stop_loss_price");
            double stop = TradeConditions.number(declared);
            boolean exactStop = java.util.stream.Stream.concat(
                    TradeConditions.groups(request.conditionPayload(), TradeConditions.TriggerType.EXIT).stream(),
                    TradeConditions.groups(request.conditionPayload(), TradeConditions.TriggerType.INVALIDATION).stream())
                    .anyMatch(group -> group.all().size() == 1 && "current_price".equals(group.all().get(0).field())
                            && "<=".equals(group.all().get(0).operator()) && TradeConditions.number(group.all().get(0).value()) == stop);
            if (stop <= 0 || request.signalPrice() == null || stop >= request.signalPrice() || !exactStop) {
                throw new IllegalArgumentException("BUY_REQUIRES_MATCHING_UNCONDITIONAL_PRICE_STOP");
            }
        }
        if (TradeConditions.groups(request.conditionPayload(), TradeConditions.TriggerType.EXIT).isEmpty()
                && TradeConditions.groups(request.conditionPayload(), TradeConditions.TriggerType.INVALIDATION).isEmpty()
                && request.plannedExitAt() == null) {
            throw new IllegalArgumentException("Position protection conditions are required");
        }
        Map<?, ?> holding = holding(account, request.stockCode());
        int held = holding == null ? 0 : Math.toIntExact(PaperAccountSnapshotService.nonnegativeLong(holding.get("quantity")));
        if (!buy && held == 0) throw new IllegalArgumentException("No actual holding exists for a management plan");
        if (buy && held > 0) throw new IllegalArgumentException("Use HOLD to protect existing holdings; pyramiding is disabled");
        List<TradeSignal> active = signals.findByUserIdAndStatusIn(request.userId(), ACTIVE);
        List<TradeSignal> matching = active.stream().filter(item -> item.getStockCode().equals(request.stockCode())).toList();
        if (matching.size() > 1) throw new IllegalStateException("DUPLICATE_ACTIVE_PLANS_REQUIRE_RECONCILIATION");
        TradeSignal signal = matching.isEmpty() ? new TradeSignal() : matching.get(0);
        if (!matching.isEmpty()) {
            if ("OPEN".equals(signal.getStatus())) {
                Double priorStop = TradeConditions.hardStop(conditions(signal));
                if (priorStop != null && (hardStop == null || hardStop < priorStop)) {
                    throw new IllegalArgumentException("OPEN_PLAN_HARD_STOP_CANNOT_BE_WEAKENED");
                }
            }
            if (v2 && signal.getAnalysisAsOf() != null && !request.analysisAsOf().isAfter(signal.getAnalysisAsOf())) {
                throw new IllegalArgumentException("STALE_ANALYSIS");
            }
            if (version <= signal.getPlanVersion()) throw new IllegalArgumentException("planVersion must increase on replacement");
            if (executions.findByUserIdAndStatusIn(request.userId(), UNRESOLVED).stream()
                    .anyMatch(item -> signal.getId().equals(item.getSignalId()))) {
                throw new IllegalStateException("ORDER_RECONCILIATION_REQUIRED_BEFORE_PLAN_UPDATE");
            }
            if ("OPEN".equals(signal.getStatus()) && buy) throw new IllegalArgumentException("An OPEN plan cannot become a new entry");
        }
        if (buy) {
            if (!Boolean.TRUE.equals(account.get("entryEligible"))) throw new IllegalStateException(String.valueOf(account.get("entryBlockReason")));
            Set<String> monitored = new HashSet<>();
            for (Object row : (List<?>) account.get("holdings")) monitored.add(String.valueOf(((Map<?, ?>) row).get("stockCode")));
            active.forEach(item -> monitored.add(item.getStockCode()));
            monitored.add(request.stockCode());
            long waiting = active.stream().filter(item -> "WAITING_ENTRY".equals(item.getStatus())
                    && !item.getStockCode().equals(request.stockCode())).count();
            if (waiting >= 5) throw new IllegalStateException("MAX_NEW_PLANS_EXCEEDED");
            if (monitored.size() > quoteCapacity) throw new IllegalStateException("PAPER_MONITOR_CAPACITY_EXCEEDED");
        }
        signal.setUserId(request.userId());
        signal.setSource(request.source());
        signal.setStrategyProfile(request.strategyProfile());
        signal.setThemeKey(request.themeKey());
        signal.setThemeName(request.themeName());
        signal.setStockCode(request.stockCode());
        signal.setStockName(request.stockName());
        signal.setAction(request.action());
        signal.setLeaderScore(request.leaderScore());
        signal.setConfidence(request.confidence());
        signal.setRiskLevel(request.riskLevel());
        signal.setPositionSize(Double.toString(target) + "%");
        signal.setSignalPrice(request.signalPrice());
        signal.setStopLoss(request.stopLoss());
        signal.setReason(request.reason());
        signal.setRawPayload(json(request.rawPayload()));
        signal.setTradePlanJson(json(request.tradePlanJson()));
        signal.setConditionPayload(json(request.conditionPayload()));
        signal.setIdempotencyKey(request.idempotencyKey());
        signal.setPlanVersion(version);
        signal.setEntryValidUntil(expiry);
        signal.setExpiresAt(expiry);
        signal.setPlannedExitAt(request.plannedExitAt());
        signal.setManagedQuantity(held);
        signal.setAccountBinding(accountGuard.binding(user));
        signal.setAnalysisAsOf(request.analysisAsOf());
        signal.setStatus(held > 0 ? "OPEN" : "WAITING_ENTRY");
        signal.setRejectReason(null);
        TradeSignal saved = signals.saveAndFlush(signal);
        receipts.saveAndFlush(new TradePlanReceipt(request.idempotencyKey(), request.userId(), saved.getId()));
        return saved;
    }

    @Transactional
    public TradeSignalExecution claim(String signalId, int version, TradeConditions.TriggerType type,
            String groupId, Map<String, Object> account, long price, long powerCash, long powerQuantity,
            Double fraction, OffsetDateTime now) {
        User user = lock(signals.ownerOf(signalId).orElseThrow());
        requireEnabledPaper(user);
        TradeSignal signal = signals.findById(signalId).orElseThrow();
        if (signal.getPlanVersion() != version || !"PAPER".equals(signal.getAccountMode())) {
            throw new IllegalStateException("STALE_PLAN_VERSION");
        }
        if (!accountGuard.binding(user).equals(signal.getAccountBinding())) {
            throw new IllegalStateException("ACCOUNT_BINDING_CHANGED");
        }
        OffsetDateTime capturedAt = OffsetDateTime.parse(String.valueOf(account.get("capturedAt")));
        if (capturedAt.isAfter(now.plusSeconds(5)) || capturedAt.isBefore(now.minusSeconds(20))) {
            throw new IllegalStateException("ACCOUNT_SNAPSHOT_STALE");
        }
        boolean buy = type == TradeConditions.TriggerType.ENTRY;
        if (buy && !"WAITING_ENTRY".equals(signal.getStatus())) throw new IllegalStateException("ENTRY_STATE_INVALID");
        if (!buy && !"OPEN".equals(signal.getStatus())) throw new IllegalStateException("EXIT_STATE_INVALID");
        if (executions.findByUserIdAndStatusIn(signal.getUserId(), UNRESOLVED).stream()
                .anyMatch(item -> signalId.equals(item.getSignalId()))) throw new IllegalStateException("ORDER_RECONCILIATION_REQUIRED");
        String base = signalId + ":" + version + ":" + type.name() + ":" + groupId;
        List<TradeSignalExecution> previous = executions.findBySignalId(signalId).stream()
                .filter(item -> item.getTriggerKey() != null && item.getTriggerKey().startsWith(base + ":")).toList();
        if (!previous.isEmpty() && (buy || type == TradeConditions.TriggerType.REDUCE)) {
            throw new IllegalStateException("TRIGGER_ALREADY_CONSUMED");
        }
        if (previous.stream().anyMatch(item -> !Set.of("CANCELLED", "REJECTED", "FILLED").contains(item.getStatus()))) {
            throw new IllegalStateException("TRIGGER_ALREADY_PENDING");
        }
        Map<?, ?> holding = holding(account, signal.getStockCode());
        long actualHeld = holding == null ? 0 : PaperAccountSnapshotService.nonnegativeLong(holding.get("quantity"));
        int quantity;
        if (buy) {
            if (signal.getEntryValidUntil() == null || !signal.getEntryValidUntil().isAfter(now)) throw new IllegalStateException("ENTRY_EXPIRED");
            Double hardStop = TradeConditions.hardStop(conditions(signal));
            if (hardStop != null && price <= hardStop) throw new IllegalStateException("ENTRY_ALREADY_AT_OR_BELOW_STOP");
            if (!Boolean.TRUE.equals(account.get("entryEligible"))) throw new IllegalStateException(String.valueOf(account.get("entryBlockReason")));
            if (actualHeld > 0) throw new IllegalStateException("HOLDING_ALREADY_EXISTS");
            if (executions.countByUserIdAndOrderSideAndSubmittedAtAfter(signal.getUserId(), "BUY",
                    now.toLocalDate().atStartOfDay(now.getOffset()).toOffsetDateTime()) >= 20) {
                throw new IllegalStateException("DAILY_BUY_LIMIT_EXCEEDED");
            }
            long reserved = executions.reservedCashForUser(signal.getUserId());
            long cash = Math.min(PaperAccountSnapshotService.nonnegativeLong(account.get("orderableCash")), powerCash);
            double equity = PaperAccountSnapshotService.positiveLong(account.get("equity"));
            double target = Double.parseDouble(signal.getPositionSize().replace("%", ""));
            long allocation = (long) Math.floor(equity * Math.min(target, PaperAccountSnapshotService.MAX_POSITION_PCT) / 100);
            long spend = Math.max(0, Math.min(allocation, cash - reserved));
            quantity = Math.toIntExact(Math.min(spend / price, powerQuantity));
        } else {
            long managed = Math.min(signal.getManagedQuantity(), actualHeld);
            signal.setManagedQuantity(Math.toIntExact(managed));
            long sellable = holding == null ? 0 : PaperAccountSnapshotService.nonnegativeLong(holding.get("sellableQuantity"));
            long desired = type == TradeConditions.TriggerType.REDUCE ? (long) Math.floor(managed * fraction) : managed;
            quantity = Math.toIntExact(Math.min(desired, sellable));
        }
        if (quantity <= 0) throw new IllegalStateException("NO_ORDERABLE_QUANTITY");
        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId(signalId);
        execution.setUserId(signal.getUserId());
        execution.setStockCode(signal.getStockCode());
        execution.setAccountBinding(signal.getAccountBinding());
        execution.setTriggerKey(base + ":" + previous.size());
        execution.setTriggerType(type.name());
        execution.setOrderSide(buy ? "BUY" : "SELL");
        execution.setOrderType("LIMIT");
        execution.setStatus("INTENT");
        execution.setQuantity(quantity);
        execution.setSubmittedQuantity(quantity);
        execution.setFilledQuantity(0);
        execution.setOrderPrice(price);
        execution.setCurrentPrice(price);
        execution.setSubmittedAt(now);
        execution.setOrderExpiresAt(now.plusMinutes(buy ? 5 : 2));
        execution.setReservedCash(buy ? Math.multiplyExact((long) quantity, price) : 0);
        signal.setRejectReason(null);
        signals.save(signal);
        return executions.saveAndFlush(execution);
    }

    @Transactional
    public void acknowledge(String executionId, Map<String, Object> response) {
        lock(executions.ownerOf(executionId).orElseThrow());
        TradeSignalExecution execution = executions.findById(executionId).orElseThrow();
        if (!Set.of("INTENT", "UNKNOWN").contains(execution.getStatus()) || execution.getOrderId() != null) return;
        execution.setKisResponse(json(response));
        Object raw = response.get("response");
        Map<?, ?> output = raw instanceof Map<?, ?> map && map.get("output") instanceof Map<?, ?> nested ? nested : Map.of();
        String orderId = text(output.get("ODNO"));
        if (orderId.isBlank()) orderId = text(output.get("odno"));
        if (Boolean.TRUE.equals(response.get("success")) && !orderId.isBlank()) {
            execution.setOrderId(orderId);
            execution.setOrderOrganization(text(output.get("KRX_FWDG_ORD_ORGNO")));
            execution.setStatus("ORDER_SUBMITTED");
        } else if (raw instanceof Map<?, ?> map && map.get("rt_cd") != null
                && !"0".equals(String.valueOf(map.get("rt_cd")))) {
            execution.setStatus("REJECTED");
            execution.setReservedCash(0L);
            execution.setRejectReason("KIS_ORDER_REJECTED");
        } else {
            execution.setStatus("UNKNOWN");
            execution.setRejectReason("ORDER_ACCEPTANCE_UNKNOWN");
        }
        executions.saveAndFlush(execution);
        TradeSignal signal = signals.findById(execution.getSignalId()).orElseThrow();
        signal.setRejectReason(Set.of("UNKNOWN", "REJECTED").contains(execution.getStatus()) ? execution.getRejectReason() : null);
        signals.save(signal);
    }

    @Transactional
    public void observeFill(String executionId, int totalFilled, long averagePrice, int remaining,
            boolean cancelled, String organization, OffsetDateTime now) {
        lock(executions.ownerOf(executionId).orElseThrow());
        TradeSignalExecution execution = executions.findById(executionId).orElseThrow();
        TradeSignal signal = signals.findById(execution.getSignalId()).orElseThrow();
        int prior = execution.getFilledQuantity() == null ? 0 : execution.getFilledQuantity();
        if (Set.of("FILLED", "CANCELLED", "REJECTED").contains(execution.getStatus())) {
            if (totalFilled > prior) throw new IllegalStateException("BROKER_TERMINAL_FILL_CONFLICT");
            return;
        }
        if (totalFilled < prior || totalFilled > execution.getSubmittedQuantity() || remaining < 0
                || totalFilled + remaining > execution.getSubmittedQuantity() || (totalFilled > 0 && averagePrice <= 0)) {
            throw new IllegalStateException("BROKER_FILL_STATE_INVALID");
        }
        int delta = totalFilled - prior;
        boolean buy = "BUY".equals(execution.getOrderSide());
        int managed = signal.getManagedQuantity() + (buy ? delta : -delta);
        if (managed < 0) throw new IllegalStateException("BROKER_FILL_EXCEEDS_MANAGED_POSITION");
        signal.setManagedQuantity(managed);
        if (managed > 0) signal.setStatus("OPEN");
        else if (!buy && totalFilled > 0) signal.setStatus("CLOSED");
        else if (buy && cancelled) signal.setStatus("EXPIRED");
        execution.setFilledQuantity(totalFilled);
        execution.setAverageFillPrice(averagePrice);
        if (totalFilled > 0) execution.setFilledAt(now);
        if (organization != null && !organization.isBlank()) execution.setOrderOrganization(organization);
        boolean filled = totalFilled == execution.getSubmittedQuantity();
        if (filled) execution.setStatus("FILLED");
        else if (cancelled && remaining == 0) execution.setStatus("CANCELLED");
        else if (!"CANCEL_REQUESTED".equals(execution.getStatus())) execution.setStatus(totalFilled > 0 ? "PARTIALLY_FILLED" : "ORDER_SUBMITTED");
        execution.setReservedCash(buy && !filled && !cancelled
                ? Math.multiplyExact((long) (execution.getSubmittedQuantity() - totalFilled), execution.getOrderPrice()) : 0);
        executions.saveAndFlush(execution);
        signals.saveAndFlush(signal);
    }

    @Transactional
    public boolean markCancelRequested(String executionId) {
        lock(executions.ownerOf(executionId).orElseThrow());
        TradeSignalExecution execution = executions.findById(executionId).orElseThrow();
        if (List.of("ORDER_SUBMITTED", "PARTIALLY_FILLED").contains(execution.getStatus())) {
            execution.setStatus("CANCEL_REQUESTED");
            executions.saveAndFlush(execution);
            return true;
        }
        return false;
    }

    @Transactional
    public void acknowledgeCancellation(String executionId, Map<String, Object> response) {
        lock(executions.ownerOf(executionId).orElseThrow());
        TradeSignalExecution execution = executions.findById(executionId).orElseThrow();
        if (!"CANCEL_REQUESTED".equals(execution.getStatus())) return;
        if (Boolean.TRUE.equals(response.get("success"))) return; // Still requires broker confirmation.
        Object raw = response.get("response");
        boolean rejected = !Boolean.TRUE.equals(response.get("unknown")) && raw instanceof Map<?, ?> body
                && body.get("rt_cd") != null && !"0".equals(String.valueOf(body.get("rt_cd")));
        if (rejected) {
            execution.setStatus(execution.getFilledQuantity() > 0 ? "PARTIALLY_FILLED" : "ORDER_SUBMITTED");
        }
        String reason = rejected ? "KIS_CANCEL_REJECTED" : "CANCEL_CONFIRMATION_REQUIRED";
        execution.setRejectReason(reason);
        executions.saveAndFlush(execution);
        TradeSignal signal = signals.findById(execution.getSignalId()).orElseThrow();
        signal.setRejectReason(reason);
        signals.save(signal);
    }

    @Transactional
    public void markUnknown(String executionId) {
        lock(executions.ownerOf(executionId).orElseThrow());
        TradeSignalExecution execution = executions.findById(executionId).orElseThrow();
        if ("INTENT".equals(execution.getStatus())) {
            execution.setStatus("UNKNOWN");
            execution.setRejectReason("RESTART_REQUIRES_BROKER_RECONCILIATION");
            executions.saveAndFlush(execution);
        }
    }

    @Transactional
    public void block(String signalId, String reason) {
        lock(signals.ownerOf(signalId).orElseThrow());
        TradeSignal signal = signals.findById(signalId).orElseThrow();
        signal.setRejectReason(reason);
        signals.save(signal);
    }

    @Transactional
    public void expireEntry(String signalId, int version, boolean invalidated, OffsetDateTime now) {
        lock(signals.ownerOf(signalId).orElseThrow());
        TradeSignal signal = signals.findById(signalId).orElseThrow();
        if (signal.getPlanVersion() != version) return;
        if (!invalidated && (signal.getEntryValidUntil() == null || signal.getEntryValidUntil().isAfter(now))) return;
        if (signal.getManagedQuantity() > 0 || !"WAITING_ENTRY".equals(signal.getStatus())) return;
        if (executions.findByUserIdAndStatusIn(signal.getUserId(), UNRESOLVED).stream()
                .anyMatch(item -> signalId.equals(item.getSignalId()))) return;
        signal.setStatus("EXPIRED");
        signal.setRejectReason("ENTRY_EXPIRED_OR_INVALIDATED");
        signals.save(signal);
    }

    private User lock(String userId) {
        return accountGuard.lock(userId);
    }
    private static void requireEnabledPaper(User user) {
        if (!user.isActive() || !user.isAutoTradeEnabled()) throw new IllegalStateException("AUTO_TRADE_DISABLED");
        if (user.getSecret() == null || user.getSecret().isKisIsReal()) throw new IllegalStateException("PAPER_ACCOUNT_REQUIRED");
    }
    @Transactional(readOnly = true)
    public java.util.Optional<TradeSignal> receipt(String key, String userId) {
        var receipt = receipts.findById(key);
        if (receipt.isEmpty()) return java.util.Optional.empty();
        if (!userId.equals(receipt.get().getUserId())) throw new IllegalArgumentException("IDEMPOTENCY_CONFLICT");
        return signals.findById(receipt.get().getSignalId());
    }
    public boolean hasReceipt(String key) { return receipts.existsById(key); }
    public int monitorCapacity() { return (int) quoteCapacity; }
    private String json(Object value) {
        try { return mapper.writeValueAsString(value); }
        catch (JsonProcessingException ex) { throw new IllegalArgumentException("Invalid JSON payload", ex); }
    }
    private Map<String, Object> conditions(TradeSignal signal) {
        try { return mapper.readValue(signal.getConditionPayload(), new com.fasterxml.jackson.core.type.TypeReference<>() { }); }
        catch (java.io.IOException ex) { throw new IllegalArgumentException("INVALID_STORED_CONDITIONS", ex); }
    }
    private static String text(Object value) { return value == null ? "" : String.valueOf(value); }
    public static Map<?, ?> holding(Map<String, Object> account, String code) {
        for (Object row : (List<?>) account.get("holdings")) {
            Map<?, ?> holding = PaperAccountSnapshotService.requireMap(row);
            if (code.equals(String.valueOf(holding.get("stockCode")))) return holding;
        }
        return null;
    }
    private static double targetPct(InternalTradeSignalRequest request) {
        if (request.targetPositionPct() != null) return TradeConditions.number(request.targetPositionPct());
        if (TradeConditions.isV2(request.conditionPayload())) throw new IllegalArgumentException("targetPositionPct is required for v2");
        if (request.positionSize() == null || !request.positionSize().matches("[0-9]+(?:\\.[0-9]+)?%")) {
            throw new IllegalArgumentException("Invalid legacy positionSize");
        }
        return Double.parseDouble(request.positionSize().replace("%", ""));
    }
}
