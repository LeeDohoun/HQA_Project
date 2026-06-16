package com.hqa.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import com.hqa.backend.repository.UserRepository;
import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TradeSignalService {

    private static final double MAX_PRICE_DRIFT_PCT = 3.0;
    private static final int DAILY_ORDER_LIMIT = 20;
    private static final double DAILY_LOSS_LIMIT_PCT = -5.0;
    private static final double MAX_POSITION_PCT = 20.0;
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");

    private final TradeSignalRepository signalRepository;
    private final TradeSignalExecutionRepository executionRepository;
    private final UserRepository userRepository;
    private final KisClient kisClient;
    private final ErrorLogger errorLogger;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    public TradeSignalService(TradeSignalRepository signalRepository,
                              TradeSignalExecutionRepository executionRepository,
                              UserRepository userRepository,
                              KisClient kisClient,
                              ErrorLogger errorLogger,
                              ObjectMapper objectMapper) {
        this(signalRepository, executionRepository, userRepository, kisClient, errorLogger, objectMapper, Clock.system(KST));
    }

    public TradeSignalService(TradeSignalRepository signalRepository,
                              TradeSignalExecutionRepository executionRepository,
                              UserRepository userRepository,
                              KisClient kisClient,
                              ErrorLogger errorLogger,
                              ObjectMapper objectMapper,
                              Clock clock) {
        this.signalRepository = signalRepository;
        this.executionRepository = executionRepository;
        this.userRepository = userRepository;
        this.kisClient = kisClient;
        this.errorLogger = errorLogger;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    @Transactional
    public TradeSignal saveSignal(InternalTradeSignalRequest request) {
        if (!isBlank(request.idempotencyKey())) {
            Optional<TradeSignal> existing = signalRepository.findByIdempotencyKey(request.idempotencyKey());
            if (existing.isPresent()) {
                return existing.get();
            }
        }
        TradeSignal signal = new TradeSignal();
        signal.setUserId(request.userId());
        signal.setSource(request.source());
        signal.setStrategyProfile(defaultString(request.strategyProfile(), "default"));
        signal.setThemeKey(request.themeKey());
        signal.setThemeName(request.themeName());
        signal.setStockCode(request.stockCode());
        signal.setStockName(request.stockName());
        signal.setAction(request.action());
        signal.setLeaderScore(request.leaderScore());
        signal.setConfidence(request.confidence());
        signal.setRiskLevel(request.riskLevel());
        signal.setPositionSize(request.positionSize());
        signal.setSignalPrice(request.signalPrice());
        signal.setStopLoss(request.stopLoss());
        signal.setReason(request.reason());
        signal.setExpiresAt(request.expiresAt());
        signal.setStatus(initialStatus(request.action()));
        signal.setRawPayload(toJson(request.rawPayload()));
        signal.setTradePlanJson(toJson(request.tradePlanJson()));
        signal.setConditionPayload(toJson(request.conditionPayload()));
        signal.setIdempotencyKey(request.idempotencyKey());
        return signalRepository.save(signal);
    }

    @Transactional(readOnly = true)
    public List<TradeSignal> recentForUser(String userId) {
        return signalRepository.findTop100ByUserIdOrderByCreatedAtDesc(userId);
    }

    @Transactional(readOnly = true)
    public boolean hasSignalWithIdempotencyKey(String idempotencyKey) {
        return !isBlank(idempotencyKey) && signalRepository.findByIdempotencyKey(idempotencyKey).isPresent();
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> recentExplanationsForUser(String userId, int limit) {
        int boundedLimit = Math.max(1, Math.min(50, limit));
        return signalRepository.findTop100ByUserIdOrderByCreatedAtDesc(userId).stream()
                .limit(boundedLimit)
                .map(this::toExplanation)
                .toList();
    }

    @Transactional
    public void processPendingSignals() {
        expireSignals("WAITING_ENTRY");
        expireSignals("WAITING_EXIT");
        expireSignals("OPEN");
        processSubmittedOrderExpirations();
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> activeSignalsForMonitor() {
        List<TradeSignal> signals = new ArrayList<>();
        signals.addAll(signalRepository.findTop100ByStatusOrderByCreatedAtAsc("WAITING_ENTRY"));
        signals.addAll(signalRepository.findTop100ByStatusOrderByCreatedAtAsc("OPEN"));
        signals.addAll(signalRepository.findTop100ByStatusOrderByCreatedAtAsc("WAITING_EXIT"));
        return signals.stream().map(this::toMonitorSignal).toList();
    }

    @Transactional
    public Optional<TradeSignal> triggerSignal(String signalId, Map<String, Object> triggerPayload) {
        Optional<TradeSignal> signal = signalRepository.findById(signalId);
        signal.ifPresent(value -> triggerSignal(value, triggerPayload));
        return signal;
    }

    @Transactional
    public void triggerSignal(TradeSignal signal, Map<String, Object> triggerPayload) {
        processOne(signal, triggerPayload);
    }

    @Transactional
    public void completeSubmittedOrder(TradeSignal signal, TradeSignalExecution execution,
                                       int filledQuantity, long averageFillPrice) {
        boolean fullyFilled = execution.getSubmittedQuantity() != null
                && filledQuantity >= execution.getSubmittedQuantity();
        String filledStatus = finalFilledStatus(signal);
        String status = fullyFilled ? filledStatus : "PARTIALLY_FILLED";
        OffsetDateTime now = OffsetDateTime.now(clock);

        signal.setStatus(status);
        if (fullyFilled) {
            signal.setExecutedAt(now);
        }
        signalRepository.save(signal);

        execution.setStatus(status);
        execution.setFilledQuantity(filledQuantity);
        execution.setAverageFillPrice(averageFillPrice);
        execution.setFilledAt(now);
        executionRepository.save(execution);
    }

    @Transactional
    public void expireSubmittedOrder(TradeSignal signal, TradeSignalExecution execution, String reason) {
        signal.setStatus("ORDER_EXPIRED");
        signal.setRejectReason(reason);
        signalRepository.save(signal);

        execution.setStatus("ORDER_EXPIRED");
        execution.setRejectReason(reason);
        executionRepository.save(execution);
    }

    @Transactional
    public void processSubmittedOrderExpirations() {
        OffsetDateTime now = OffsetDateTime.now(clock);
        for (TradeSignalExecution execution : executionRepository
                .findTop100ByStatusAndOrderExpiresAtBeforeOrderByOrderExpiresAtAsc("ORDER_SUBMITTED", now)) {
            signalRepository.findById(execution.getSignalId())
                    .ifPresent(signal -> expireSubmittedOrder(signal, execution, "ORDER_NOT_FILLED"));
        }
    }

    private void expireSignals(String status) {
        for (TradeSignal signal : signalRepository.findTop100ByStatusOrderByCreatedAtAsc(status)) {
            if (signal.getExpiresAt() != null && signal.getExpiresAt().isBefore(OffsetDateTime.now())) {
                mark(signal, "EXPIRED", "SIGNAL_EXPIRED", null, null, null, null);
            }
        }
    }

    private void processOne(TradeSignal signal, Map<String, Object> triggerPayload) {
        if (signal.getExpiresAt() != null && signal.getExpiresAt().isBefore(OffsetDateTime.now())) {
            mark(signal, "EXPIRED", "SIGNAL_EXPIRED", null, null, null, null);
            return;
        }

        boolean buy = signal.getAction() != null && signal.getAction().contains("BUY");
        if (signal.getId() != null && executionRepository.existsBySignalIdAndStatus(signal.getId(), "ORDER_SUBMITTED")) {
            mark(signal, "REJECTED", "DUPLICATE_ORDER", null, null, null, null, toJson(triggerPayload));
            return;
        }
        if (executionRepository.countByUserIdAndExecutedAtAfter(signal.getUserId(), OffsetDateTime.now(clock).toLocalDate().atStartOfDay(KST).toOffsetDateTime()) >= DAILY_ORDER_LIMIT) {
            mark(signal, "REJECTED", "DAILY_ORDER_LIMIT_EXCEEDED", null, null, null, null, toJson(triggerPayload));
            return;
        }
        if (!marketHoursAllowed(clock)) {
            mark(signal, "REJECTED", "MARKET_CLOSED", null, null, null, null, toJson(triggerPayload));
            return;
        }

        Optional<User> maybeUser = userRepository.findByUserId(signal.getUserId());
        if (maybeUser.isEmpty() || !maybeUser.get().isActive() || !maybeUser.get().isAutoTradeEnabled()) {
            mark(signal, "REJECTED", "AUTO_TRADE_DISABLED", null, null, null, null);
            return;
        }

        User user = maybeUser.get();
        UserSecret secret = user.getSecret();
        if (secret == null || isBlank(secret.getKisAppKey()) || isBlank(secret.getKisAppSecret())
                || isBlank(secret.getKisAccountNo())) {
            mark(signal, "REJECTED", "KIS_SECRET_MISSING", null, null, null, null);
            return;
        }

        String token = kisClient.fetchAccessToken(user.getUserId(), secret);
        if (token == null) {
            mark(signal, "FAILED", "KIS_TOKEN_UNAVAILABLE", null, null, null, null);
            return;
        }

        Map<String, Object> balance = kisClient.inquireBalance(user.getUserId(), secret, token);
        if (!Boolean.TRUE.equals(balance.get("success"))) {
            mark(signal, "FAILED", "KIS_BALANCE_UNAVAILABLE", null, null, null, null, toJson(balance));
            return;
        }
        if (dailyLossPct(balance) <= DAILY_LOSS_LIMIT_PCT) {
            mark(signal, "REJECTED", "DAILY_LOSS_LIMIT_EXCEEDED", null, null, null, null, toJson(balance));
            return;
        }

        Long currentPrice = kisClient.inquireCurrentPrice(user.getUserId(), secret, token, signal.getStockCode());
        if (currentPrice == null || currentPrice <= 0) {
            mark(signal, "REJECTED", "CURRENT_PRICE_UNAVAILABLE", null, null, currentPrice, null);
            return;
        }

        Double drift = priceDrift(signal.getSignalPrice(), currentPrice);
        if (drift != null && drift > MAX_PRICE_DRIFT_PCT) {
            mark(signal, "REJECTED", "PRICE_DRIFT_EXCEEDED", null, null, currentPrice, drift);
            return;
        }

        int quantity = buy ? 1 : holdingQuantity(balance, signal.getStockCode());
        if (quantity <= 0) {
            mark(signal, "REJECTED", buy ? "INVALID_ORDER_QUANTITY" : "NO_SELLABLE_HOLDING", null, currentPrice, currentPrice, drift);
            return;
        }
        if (buy && requestedPositionPct(signal.getPositionSize()) > MAX_POSITION_PCT) {
            mark(signal, "REJECTED", "MAX_POSITION_EXCEEDED", quantity, currentPrice, currentPrice, drift);
            return;
        }
        if (buy && availableCash(balance) < currentPrice) {
            mark(signal, "REJECTED", "INSUFFICIENT_CASH", quantity, currentPrice, currentPrice, drift);
            return;
        }

        Map<String, Object> result;
        try {
            result = buy
                    ? kisClient.buy(user.getUserId(), secret, token, signal.getStockCode(), quantity, currentPrice)
                    : kisClient.sell(user.getUserId(), secret, token, signal.getStockCode(), quantity, currentPrice);
        } catch (Exception e) {
            errorLogger.log("TradeSignalService", user.getUserId(), signal.getStockCode(), "KIS order failed", e.getMessage());
            mark(signal, "FAILED", "KIS_ORDER_FAILED", quantity, currentPrice, currentPrice, drift);
            return;
        }

        boolean success = Boolean.TRUE.equals(result.get("success"));
        if (success) {
            markOrderSubmitted(signal, quantity, currentPrice, currentPrice, drift, toJson(result), orderId(result), buy);
            return;
        }
        mark(
                signal,
                "FAILED",
                "KIS_ORDER_FAILED",
                quantity,
                currentPrice,
                currentPrice,
                drift,
                toJson(result)
        );
    }

    private void mark(TradeSignal signal, String status, String reason, Integer quantity,
                      Long orderPrice, Long currentPrice, Double priceDriftPct) {
        mark(signal, status, reason, quantity, orderPrice, currentPrice, priceDriftPct, null);
    }

    private void mark(TradeSignal signal, String status, String reason, Integer quantity,
                      Long orderPrice, Long currentPrice, Double priceDriftPct, String kisResponse) {
        signal.setStatus(status);
        signal.setRejectReason(reason);
        if ("EXECUTED".equals(status) || "OPEN".equals(status) || "CLOSED".equals(status)) {
            signal.setExecutedAt(OffsetDateTime.now());
        }
        signalRepository.save(signal);

        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId(signal.getId());
        execution.setUserId(signal.getUserId());
        execution.setStatus(status);
        execution.setRejectReason(reason);
        execution.setQuantity(quantity);
        execution.setOrderPrice(orderPrice);
        execution.setCurrentPrice(currentPrice);
        execution.setPriceDriftPct(priceDriftPct);
        execution.setKisResponse(kisResponse);
        executionRepository.save(execution);
    }

    private void markOrderSubmitted(TradeSignal signal, Integer quantity, Long orderPrice,
                                    Long currentPrice, Double priceDriftPct, String kisResponse,
                                    String orderId, boolean buy) {
        signal.setStatus("ORDER_SUBMITTED");
        signal.setRejectReason(null);
        signalRepository.save(signal);

        OffsetDateTime now = OffsetDateTime.now(clock);
        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId(signal.getId());
        execution.setUserId(signal.getUserId());
        execution.setStatus("ORDER_SUBMITTED");
        execution.setOrderId(orderId);
        execution.setOrderType(orderPrice == null || orderPrice <= 0 ? "MARKET" : "LIMIT");
        execution.setQuantity(quantity);
        execution.setSubmittedQuantity(quantity);
        execution.setFilledQuantity(0);
        execution.setOrderPrice(orderPrice);
        execution.setCurrentPrice(currentPrice);
        execution.setPriceDriftPct(priceDriftPct);
        execution.setKisResponse(kisResponse);
        execution.setSubmittedAt(now);
        execution.setOrderExpiresAt(now.plusMinutes(buy ? 5 : 2));
        executionRepository.save(execution);
    }

    private Double priceDrift(Long signalPrice, Long currentPrice) {
        if (signalPrice == null || signalPrice <= 0 || currentPrice == null || currentPrice <= 0) {
            return null;
        }
        return Math.abs((currentPrice - signalPrice) * 100.0 / signalPrice);
    }

    @SuppressWarnings("unchecked")
    private long availableCash(Map<String, Object> balance) {
        Object summary = balance.get("summary");
        if (summary instanceof Map<?, ?> map) {
            Object deposit = ((Map<String, Object>) map).get("deposit");
            if (deposit instanceof Number number) {
                return number.longValue();
            }
            try {
                return deposit == null ? 0L : Long.parseLong(String.valueOf(deposit));
            } catch (NumberFormatException ignored) {
                return 0L;
            }
        }
        return 0L;
    }

    @SuppressWarnings("unchecked")
    private double dailyLossPct(Map<String, Object> balance) {
        Object summary = balance.get("summary");
        if (summary instanceof Map<?, ?> map) {
            Map<String, Object> typed = (Map<String, Object>) map;
            for (String key : List.of("dailyLossPct", "dayLossPct", "pnlRate")) {
                Object value = typed.get(key);
                if (value instanceof Number number) {
                    return number.doubleValue();
                }
                try {
                    if (value != null && !String.valueOf(value).isBlank()) {
                        return Double.parseDouble(String.valueOf(value));
                    }
                } catch (NumberFormatException ignored) {
                    return 0.0;
                }
            }
        }
        return 0.0;
    }

    private double requestedPositionPct(String positionSize) {
        if (positionSize == null || positionSize.isBlank()) {
            return 0.0;
        }
        try {
            return Double.parseDouble(positionSize.replace("%", "").trim());
        } catch (NumberFormatException ignored) {
            return 0.0;
        }
    }

    @SuppressWarnings("unchecked")
    private int holdingQuantity(Map<String, Object> balance, String stockCode) {
        Object holdings = balance.get("holdings");
        if (!(holdings instanceof List<?> rows)) {
            return 0;
        }
        for (Object row : rows) {
            if (!(row instanceof Map<?, ?> map)) {
                continue;
            }
            Map<String, Object> holding = (Map<String, Object>) map;
            if (!stockCode.equals(String.valueOf(holding.get("stockCode")))) {
                continue;
            }
            Object quantity = holding.get("quantity");
            if (quantity instanceof Number number) {
                return Math.max(0, number.intValue());
            }
            try {
                return quantity == null ? 0 : Math.max(0, Integer.parseInt(String.valueOf(quantity)));
            } catch (NumberFormatException ignored) {
                return 0;
            }
        }
        return 0;
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value == null ? Map.of() : value);
        } catch (Exception e) {
            return "{}";
        }
    }

    private Map<String, Object> toMonitorSignal(TradeSignal signal) {
        Map<String, Object> item = new HashMap<>();
        item.put("signalId", signal.getId());
        item.put("userId", signal.getUserId());
        item.put("status", signal.getStatus());
        item.put("stockCode", signal.getStockCode());
        item.put("stockName", signal.getStockName());
        item.put("action", signal.getAction());
        item.put("signalPrice", signal.getSignalPrice());
        item.put("tradePlanJson", parseJsonMap(signal.getTradePlanJson()));
        item.put("conditionPayload", parseJsonMap(signal.getConditionPayload()));
        return item;
    }

    private Map<String, Object> toExplanation(TradeSignal signal) {
        JsonNode raw = parseRawPayload(signal.getRawPayload());
        JsonNode leader = raw.path("leader").isMissingNode() ? raw : raw.path("leader");
        JsonNode rank = raw.path("rank");
        JsonNode finalDecision = firstObject(
                leader.path("final_decision"),
                rank.path("final_decision")
        );
        List<TradeSignalExecution> executions = signal.getId() == null
                ? List.of()
                : executionRepository.findBySignalIdOrderByExecutedAtDesc(signal.getId());
        TradeSignalExecution latest = executions.stream()
                .max(Comparator.comparing(TradeSignalExecution::getExecutedAt, Comparator.nullsLast(Comparator.naturalOrder())))
                .orElse(null);

        Map<String, Object> item = new HashMap<>();
        item.put("signalId", signal.getId());
        item.put("source", signal.getSource());
        item.put("strategyProfile", signal.getStrategyProfile());
        item.put("themeName", signal.getThemeName());
        item.put("stockCode", signal.getStockCode());
        item.put("stockName", signal.getStockName());
        item.put("action", signal.getAction());
        item.put("leaderScore", valueOrZero(signal.getLeaderScore()));
        item.put("confidence", valueOrZero(signal.getConfidence()));
        item.put("riskLevel", blankToFallback(signal.getRiskLevel(), text(finalDecision, "risk_level_code", "")));
        item.put("positionSize", blankToFallback(signal.getPositionSize(), text(finalDecision, "position_size", "")));
        item.put("signalPrice", signal.getSignalPrice());
        item.put("stopLoss", blankToFallback(signal.getStopLoss(), text(finalDecision, "stop_loss", "")));
        item.put("reason", firstText(signal.getReason(), text(finalDecision, "summary", "")));
        item.put("tradePlanJson", parseJsonMap(signal.getTradePlanJson()));
        item.put("conditionPayload", parseJsonMap(signal.getConditionPayload()));
        item.put("idempotencyKey", signal.getIdempotencyKey());
        item.put("status", signal.getStatus());
        item.put("rejectReason", signal.getRejectReason());
        item.put("createdAt", signal.getCreatedAt());
        item.put("updatedAt", signal.getUpdatedAt());
        item.put("executedAt", signal.getExecutedAt());
        item.put("executionStatus", latest == null ? null : latest.getStatus());
        item.put("executionRejectReason", latest == null ? null : latest.getRejectReason());
        item.put("orderId", latest == null ? null : latest.getOrderId());
        item.put("orderType", latest == null ? null : latest.getOrderType());
        item.put("quantity", latest == null ? null : latest.getQuantity());
        item.put("submittedQuantity", latest == null ? null : latest.getSubmittedQuantity());
        item.put("filledQuantity", latest == null ? null : latest.getFilledQuantity());
        item.put("orderPrice", latest == null ? null : latest.getOrderPrice());
        item.put("averageFillPrice", latest == null ? null : latest.getAverageFillPrice());
        item.put("currentPrice", latest == null ? null : latest.getCurrentPrice());
        item.put("priceDriftPct", latest == null ? null : latest.getPriceDriftPct());
        item.put("submittedAt", latest == null ? null : latest.getSubmittedAt());
        item.put("filledAt", latest == null ? null : latest.getFilledAt());
        item.put("orderExpiresAt", latest == null ? null : latest.getOrderExpiresAt());
        item.put("explanationSummary", explanationSummary(signal, finalDecision));
        item.put("catalysts", stringList(finalDecision.path("key_catalysts")));
        item.put("risks", stringList(finalDecision.path("risk_factors")));
        item.put("agentReasons", agentReasons(leader, finalDecision));
        return item;
    }

    private JsonNode parseRawPayload(String rawPayload) {
        if (rawPayload == null || rawPayload.isBlank()) {
            return objectMapper.createObjectNode();
        }
        try {
            return objectMapper.readTree(rawPayload);
        } catch (Exception ignored) {
            return objectMapper.createObjectNode();
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> parseJsonMap(String rawPayload) {
        if (rawPayload == null || rawPayload.isBlank()) {
            return Map.of();
        }
        try {
            Object parsed = objectMapper.readValue(rawPayload, Object.class);
            if (parsed instanceof Map<?, ?> map) {
                return (Map<String, Object>) map;
            }
        } catch (Exception ignored) {
            return Map.of();
        }
        return Map.of();
    }

    private List<Map<String, Object>> agentReasons(JsonNode leader, JsonNode finalDecision) {
        List<Map<String, Object>> reasons = new ArrayList<>();
        addAgentReason(reasons, "analyst", "Analyst", leader.path("analyst"),
                List.of("summary", "final_opinion", "opinion"),
                List.of("hegemony_grade", "grade"),
                List.of("total_score"));
        addAgentReason(reasons, "quant", "Quant", leader.path("quant"),
                List.of("opinion", "summary"),
                List.of("grade"),
                List.of("total_score"));
        addAgentReason(reasons, "chartist", "Chartist", leader.path("chartist"),
                List.of("short_term_opinion", "mid_term_opinion", "opinion", "summary"),
                List.of("signal"),
                List.of("total_score"));
        addAgentReason(reasons, "risk_manager", "Risk Manager", finalDecision,
                List.of("summary", "detailed_reasoning"),
                List.of("action_code", "action"),
                List.of("total_score", "confidence"));
        return reasons;
    }

    private void addAgentReason(List<Map<String, Object>> reasons, String agent, String label, JsonNode node,
                                List<String> summaryKeys, List<String> verdictKeys, List<String> scoreKeys) {
        if (node == null || node.isMissingNode() || node.isNull() || !node.isObject()) {
            return;
        }
        String summary = firstNodeText(node, summaryKeys);
        String verdict = firstNodeText(node, verdictKeys);
        Integer score = firstNodeInt(node, scoreKeys);
        if (summary.isBlank() && verdict.isBlank() && score == null) {
            return;
        }
        Map<String, Object> reason = new HashMap<>();
        reason.put("agent", agent);
        reason.put("label", label);
        reason.put("summary", summary);
        reason.put("verdict", verdict);
        reason.put("score", score);
        reasons.add(reason);
    }

    private String explanationSummary(TradeSignal signal, JsonNode finalDecision) {
        String summary = firstText(signal.getReason(), text(finalDecision, "summary", ""));
        if (!summary.isBlank()) {
            return summary;
        }
        return "%s 판단, 신뢰도 %d%%, 리스크 %s".formatted(
                blankToFallback(signal.getAction(), "-"),
                valueOrZero(signal.getConfidence()),
                blankToFallback(signal.getRiskLevel(), "-")
        );
    }

    private JsonNode firstObject(JsonNode... nodes) {
        for (JsonNode node : nodes) {
            if (node != null && node.isObject()) {
                return node;
            }
        }
        return objectMapper.createObjectNode();
    }

    private List<String> stringList(JsonNode node) {
        if (node == null || !node.isArray()) {
            return List.of();
        }
        List<String> values = new ArrayList<>();
        for (JsonNode item : node) {
            if (item.isTextual() && !item.asText().isBlank()) {
                values.add(item.asText());
            }
        }
        return values;
    }

    private String firstNodeText(JsonNode node, List<String> keys) {
        for (String key : keys) {
            String value = text(node, key, "");
            if (!value.isBlank()) {
                return value;
            }
        }
        return "";
    }

    private Integer firstNodeInt(JsonNode node, List<String> keys) {
        for (String key : keys) {
            JsonNode value = node.path(key);
            if (value.isNumber()) {
                return value.asInt();
            }
            if (value.isTextual() && !value.asText().isBlank()) {
                try {
                    return Integer.parseInt(value.asText());
                } catch (NumberFormatException ignored) {
                    return null;
                }
            }
        }
        return null;
    }

    private String text(JsonNode node, String key, String fallback) {
        if (node == null || node.isMissingNode() || node.isNull()) {
            return fallback;
        }
        JsonNode value = node.path(key);
        if (value.isMissingNode() || value.isNull()) {
            return fallback;
        }
        return value.asText(fallback);
    }

    private String firstText(String preferred, String fallback) {
        return preferred == null || preferred.isBlank() ? blankToFallback(fallback, "") : preferred;
    }

    private String blankToFallback(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private int valueOrZero(Integer value) {
        return value == null ? 0 : value;
    }

    private static String defaultString(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private static String initialStatus(String action) {
        String normalized = action == null ? "" : action.toUpperCase();
        if (normalized.contains("BUY")) {
            return "WAITING_ENTRY";
        }
        return "WAITING_EXIT";
    }

    private static String finalFilledStatus(TradeSignal signal) {
        String normalized = signal.getAction() == null ? "" : signal.getAction().toUpperCase();
        return normalized.contains("BUY") ? "OPEN" : "CLOSED";
    }

    private static boolean marketHoursAllowed(Clock clock) {
        java.time.ZonedDateTime now = java.time.ZonedDateTime.now(clock);
        java.time.DayOfWeek day = now.getDayOfWeek();
        if (day == java.time.DayOfWeek.SATURDAY || day == java.time.DayOfWeek.SUNDAY) {
            return false;
        }
        java.time.LocalTime time = now.toLocalTime();
        return !time.isBefore(java.time.LocalTime.of(9, 0)) && !time.isAfter(java.time.LocalTime.of(15, 30));
    }

    @SuppressWarnings("unchecked")
    private static String orderId(Map<String, Object> result) {
        Object response = result.get("response");
        if (response instanceof Map<?, ?> responseMap) {
            Object output = responseMap.get("output");
            if (output instanceof Map<?, ?> outputMap) {
                for (String key : List.of("ODNO", "odno", "orderId", "order_id")) {
                    Object value = outputMap.get(key);
                    if (value != null && !String.valueOf(value).isBlank()) {
                        return String.valueOf(value);
                    }
                }
            }
            for (String key : List.of("ODNO", "odno", "orderId", "order_id")) {
                Object value = responseMap.get(key);
                if (value != null && !String.valueOf(value).isBlank()) {
                    return String.valueOf(value);
                }
            }
        }
        Object direct = result.get("orderId");
        return direct == null || String.valueOf(direct).isBlank() ? null : String.valueOf(direct);
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
