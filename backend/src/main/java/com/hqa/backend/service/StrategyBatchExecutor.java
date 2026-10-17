package com.hqa.backend.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.StrategyProperties;
import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyBatchRepository;
import java.time.OffsetDateTime;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Sends QUEUED strategy batches inside their auction window, SELL orders before BUY orders. Each order
 * is recorded as UNKNOWN before the broker call, so a crash or an ambiguous answer is never resent;
 * only a broker answer that the order was not taken (rate limit, local queue full) returns it to PENDING.
 * Nothing is sent while {@code hqa.strategy.orders-enabled} is false.
 */
@Component
public class StrategyBatchExecutor {
    private static final Logger log = LoggerFactory.getLogger(StrategyBatchExecutor.class);
    private final StrategyProperties properties;
    private final StrategyBatchRepository batches;
    private final StrategyBatchOrderRepository batchOrders;
    private final StrategyAccountRepository accounts;
    private final StrategyBatchService batchService;
    private final StrategyRiskGuard risk;
    private final StrategySessionClock sessions;
    private final PaperAccountSnapshotService snapshots;
    private final KisClient kis;
    private final ObjectMapper mapper;
    private final Map<String, CachedRisk> riskCache = new ConcurrentHashMap<>();
    private final StrategyWorker worker = new StrategyWorker("strategy-batch-executor");

    private record CachedRisk(StrategyRiskGuard.RiskState state, OffsetDateTime at) { }

    enum Outcome { SUBMITTED, REJECTED, UNKNOWN, RETRY }

    public StrategyBatchExecutor(StrategyProperties properties, StrategyBatchRepository batches,
            StrategyBatchOrderRepository batchOrders, StrategyAccountRepository accounts, StrategyBatchService batchService,
            StrategyRiskGuard risk, StrategySessionClock sessions, PaperAccountSnapshotService snapshots, KisClient kis,
            ObjectMapper mapper) {
        this.properties = properties;
        this.batches = batches;
        this.batchOrders = batchOrders;
        this.accounts = accounts;
        this.batchService = batchService;
        this.risk = risk;
        this.sessions = sessions;
        this.snapshots = snapshots;
        this.kis = kis;
        this.mapper = mapper;
    }

    /** Hands the run to the executor's own thread; the shared scheduler thread returns at once. */
    @Scheduled(fixedDelayString = "${hqa.strategy.executor-poll-ms:2000}")
    public void tick() {
        if (!properties.isOrdersEnabled()) return;
        worker.submit(this::safeRun);
    }

    void safeRun() {
        try {
            runOnce();
        } catch (RuntimeException ex) {
            log.error("[StrategyBatchExecutor] batch execution failed: {}", ex.getMessage());
        }
    }

    @PreDestroy
    public void shutdown() {
        worker.close();
    }

    public void runOnce() {
        if (!properties.isOrdersEnabled()) return;
        OffsetDateTime start = sessions.now();
        OffsetDateTime deadline = start.plusNanos(properties.getExecutorTickBudgetMs() * 1_000_000L);
        for (StrategyBatch batch : batches.findBySessionDateAndStatusInOrderByCreatedAtAsc(start.toLocalDate(),
                List.of("QUEUED", "SUBMITTING"))) {
            try {
                OffsetDateTime now = sessions.now();
                if (sessions.phaseWindowClosed(batch.getSessionDate(), batch.getPhase(), now)) {
                    batchService.closeWindow(batch, now);
                    continue;
                }
                if (!sessions.inPhaseWindow(batch.getSessionDate(), batch.getPhase(), now)) continue;
                if (!execute(batch, deadline)) return;
            } catch (ObjectOptimisticLockingFailureException ex) {
                log.warn("[StrategyBatchExecutor] batch {} changed concurrently; retrying next tick", batch.getId());
            }
        }
    }

    /** @return false when the tick budget ran out and the remaining batches wait for the next tick */
    boolean execute(StrategyBatch batch, OffsetDateTime deadline) {
        OffsetDateTime now = sessions.now();
        List<StrategyBatchOrder> orders = batchOrders.findByBatchIdOrderBySeqAsc(batch.getId());
        User user;
        String block = null;
        try {
            user = snapshots.paperUser(batch.getUserId());
            if (!snapshots.binding(user).equals(batch.getAccountBinding())) block = "ACCOUNT_BINDING_CHANGED";
        } catch (RuntimeException ex) {
            user = null;
            block = "PAPER_ACCOUNT_REQUIRED";
        }
        if (block == null) block = accountBlock(batch);
        if (block != null) {
            skipPending(orders, block, now);
            batch.setStatus("SUBMITTED");
            if (batch.getSubmittedAt() == null) batch.setSubmittedAt(now);
            batches.saveAndFlush(batch);
            return true;
        }
        if (risk.isHalted(batch.getUserId())) {
            halt(batch, orders, now);
            return true;
        }
        if ("QUEUED".equals(batch.getStatus())) {
            batch.setStatus("SUBMITTING");
            batch = batches.saveAndFlush(batch);
        }
        String token = kis.fetchAccessToken(batch.getUserId(), user.getSecret());
        if (token == null || token.isBlank()) {
            log.warn("[StrategyBatchExecutor] KIS token unavailable for batch {}", batch.getId());
            return true;
        }
        List<StrategyBatchOrder> pending = orders.stream().filter(order -> "PENDING".equals(order.getStatus()))
                .sorted(Comparator.comparing(StrategyBatchOrder::isBuy).thenComparingInt(StrategyBatchOrder::getSeq)).toList();
        for (StrategyBatchOrder order : pending) {
            now = sessions.now();
            if (!sessions.inPhaseWindow(batch.getSessionDate(), batch.getPhase(), now)) return true; // closed next tick
            if (now.isAfter(deadline)) return false;
            if (risk.isHalted(batch.getUserId())) {
                halt(batch, orders, now);
                return true;
            }
            if (order.isBuy()) {
                StrategyRiskGuard.RiskState state = currentRisk(batch.getUserId(), now);
                if (!state.buyAllowed()) {
                    skip(order, state.buyBlockReason(), now);
                    continue;
                }
            }
            if (send(batch, user, token, order) == Outcome.RETRY) return true;
        }
        if (orders.stream().noneMatch(order -> "PENDING".equals(order.getStatus()))) {
            batch.setStatus("SUBMITTED");
            batch.setSubmittedAt(sessions.now());
            batches.saveAndFlush(batch);
        }
        return true;
    }

    private String accountBlock(StrategyBatch batch) {
        StrategyAccount account = accounts.findById(batch.getUserId()).orElse(null);
        if (account == null) return "STRATEGY_ACCOUNT_NOT_FOUND";
        if (!account.getStrategyId().equals(batch.getStrategyId())) return "STRATEGY_MISMATCH";
        if (!"paper".equals(account.getStage())) return "STRATEGY_STAGE_NOT_PAPER";
        if (!account.isEnabled()) return "STRATEGY_ACCOUNT_DISABLED";
        return null;
    }

    private StrategyRiskGuard.RiskState currentRisk(String userId, OffsetDateTime now) {
        CachedRisk cached = riskCache.get(userId);
        if (cached == null || cached.at().plusSeconds(properties.getRiskRecheckSeconds()).isBefore(now)) {
            cached = new CachedRisk(risk.evaluate(userId), now);
            riskCache.put(userId, cached);
        }
        return cached.state();
    }

    Outcome send(StrategyBatch batch, User user, String token, StrategyBatchOrder order) {
        OffsetDateTime now = sessions.now();
        // Intent first: if the process dies during the call the order stays UNKNOWN and is never resent.
        order.setStatus("UNKNOWN");
        order.setRejectReason("ORDER_ACCEPTANCE_UNKNOWN");
        order.setOrderType(properties.getOrderType());
        order.setSubmittedAt(now);
        order.setOrderDate(now.toLocalDate());
        order.setUpdatedAt(now);
        batchOrders.saveAndFlush(order);
        Map<String, Object> response;
        try {
            response = kis.paperAuctionOrder(batch.getUserId(), user.getSecret(), token, order.getStockCode(),
                    Math.toIntExact(order.getQuantity()), order.getSide());
        } catch (IllegalArgumentException ex) {
            // Raised by the client's own checks before any HTTP request.
            order.setStatus("REJECTED");
            order.setRejectReason("ORDER_NOT_SENT_INVALID");
            order.setKisResponse(json(Map.of("error", String.valueOf(ex.getMessage()))));
            order.setUpdatedAt(sessions.now());
            batchOrders.saveAndFlush(order);
            return Outcome.REJECTED;
        } catch (RuntimeException ex) {
            order.setKisResponse(json(Map.of("error", ex.getClass().getSimpleName())));
            order.setUpdatedAt(sessions.now());
            batchOrders.saveAndFlush(order);
            return Outcome.UNKNOWN;
        }
        Outcome outcome = acknowledge(order, response == null ? Map.of() : response);
        order.setUpdatedAt(sessions.now());
        batchOrders.saveAndFlush(order);
        return outcome;
    }

    /** Same interpretation as PaperTradeStore.acknowledge for LLM orders. */
    Outcome acknowledge(StrategyBatchOrder order, Map<String, Object> response) {
        order.setKisResponse(json(response));
        Object raw = response.get("response");
        Map<?, ?> output = raw instanceof Map<?, ?> map && map.get("output") instanceof Map<?, ?> nested ? nested : Map.of();
        String orderId = text(output.get("ODNO"));
        if (orderId.isBlank()) orderId = text(output.get("odno"));
        if (Boolean.TRUE.equals(response.get("success")) && !orderId.isBlank()) {
            order.setStatus("SUBMITTED");
            order.setBrokerOrderId(orderId);
            String organization = text(output.get("KRX_FWDG_ORD_ORGNO"));
            order.setOrderOrg(organization.isBlank() ? null : organization);
            order.setRejectReason(null);
            return Outcome.SUBMITTED;
        }
        if (Boolean.TRUE.equals(response.get("notSent"))) {
            // The local pacing queue refused the request before it was sent.
            order.setStatus("PENDING");
            order.setSubmittedAt(null);
            order.setOrderDate(null);
            order.setRejectReason("ORDER_NOT_SENT_RATE_QUEUE_FULL");
            return Outcome.RETRY;
        }
        if (raw instanceof Map<?, ?> map && map.get("rt_cd") != null && !"0".equals(String.valueOf(map.get("rt_cd")))) {
            if ("EGW00201".equals(String.valueOf(map.get("msg_cd")))) {
                // KIS per-second limit: the order itself was not judged; send it again on the next tick.
                order.setStatus("PENDING");
                order.setSubmittedAt(null);
                order.setOrderDate(null);
                order.setRejectReason("KIS_RATE_LIMITED");
                return Outcome.RETRY;
            }
            order.setStatus("REJECTED");
            order.setRejectReason("KIS_ORDER_REJECTED");
            return Outcome.REJECTED;
        }
        order.setStatus("UNKNOWN");
        order.setRejectReason("ORDER_ACCEPTANCE_UNKNOWN");
        return Outcome.UNKNOWN;
    }

    private void halt(StrategyBatch batch, List<StrategyBatchOrder> orders, OffsetDateTime now) {
        skipPending(orders, "TRADING_HALTED", now);
        batch.setStatus("HALTED");
        if (batch.getSubmittedAt() == null) batch.setSubmittedAt(now);
        batches.saveAndFlush(batch);
    }

    private void skipPending(List<StrategyBatchOrder> orders, String reason, OffsetDateTime now) {
        for (StrategyBatchOrder order : orders) {
            if ("PENDING".equals(order.getStatus())) skip(order, reason, now);
        }
    }

    private void skip(StrategyBatchOrder order, String reason, OffsetDateTime now) {
        order.setStatus("SKIPPED");
        order.setRejectReason(reason);
        order.setUpdatedAt(now);
        batchOrders.saveAndFlush(order);
    }

    private String json(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            return String.valueOf(value);
        }
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }
}
