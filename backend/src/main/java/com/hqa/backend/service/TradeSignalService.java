package com.hqa.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
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
    private final TradeSignalRepository signalRepository;
    private final TradeSignalExecutionRepository executionRepository;
    private final ObjectMapper objectMapper;
    private final PaperTradeLifecycle lifecycle;

    public TradeSignalService(TradeSignalRepository signalRepository,
            TradeSignalExecutionRepository executionRepository, ObjectMapper objectMapper, PaperTradeLifecycle lifecycle) {
        this.signalRepository = signalRepository;
        this.executionRepository = executionRepository;
        this.objectMapper = objectMapper;
        this.lifecycle = lifecycle;
    }

    public TradeSignal saveSignal(InternalTradeSignalRequest request) {
        return lifecycle.save(request);
    }

    @Transactional(readOnly = true)
    public List<TradeSignal> recentForUser(String userId) {
        return signalRepository.findTop100ByUserIdOrderByCreatedAtDesc(userId);
    }

    @Transactional(readOnly = true)
    public boolean hasSignalWithIdempotencyKey(String idempotencyKey) {
        return idempotencyKey != null && !idempotencyKey.isBlank()
                && lifecycle.hasReceipt(idempotencyKey);
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> recentExplanationsForUser(String userId, int limit) {
        return signalRepository.findTop100ByUserIdOrderByCreatedAtDesc(userId).stream()
                .limit(Math.max(1, Math.min(50, limit))).map(this::toExplanation).toList();
    }

    public void processPendingSignals() { lifecycle.expireEntries(); }
    public void processSubmittedOrderExpirations() { lifecycle.reconcilePendingOrders(); }
    public List<Map<String, Object>> activeSignalsForMonitor() { return lifecycle.allActive(); }
    public Map<String, Object> activeSignalsForMonitor(int page, int size) { return lifecycle.active(page, size); }
    public Optional<TradeSignal> triggerSignal(String signalId, Map<String, Object> payload) {
        return lifecycle.trigger(signalId, payload);
    }
    public Map<String, Object> triggerResponse(String signalId, Map<String, Object> payload) {
        return lifecycle.triggerResponse(signalId, payload);
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


}
