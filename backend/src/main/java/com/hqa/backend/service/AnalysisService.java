package com.hqa.backend.service;

import com.hqa.backend.dto.*;
import com.hqa.backend.exception.ApiException;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.connection.Message;
import org.springframework.data.redis.connection.MessageListener;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.listener.ChannelTopic;
import org.springframework.data.redis.listener.RedisMessageListenerContainer;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Service
public class AnalysisService {

    private final AiServerClient aiServerClient;
    private final StockCatalogService stockCatalogService;
    private final StringRedisTemplate redisTemplate;
    private final Map<String, TaskMeta> tasks = new LinkedHashMap<>();
    private final Map<String, List<SseEmitter>> emitters = new ConcurrentHashMap<>();
    private final Map<String, List<Map<String, Object>>> progressEvents = new ConcurrentHashMap<>();
    private final Map<String, RedisMessageListenerContainer> progressCaptureContainers = new ConcurrentHashMap<>();

    public AnalysisService(AiServerClient aiServerClient, StockCatalogService stockCatalogService) {
        this(aiServerClient, stockCatalogService, null);
    }

    @Autowired
    public AnalysisService(AiServerClient aiServerClient, StockCatalogService stockCatalogService,
                           StringRedisTemplate redisTemplate) {
        this.aiServerClient = aiServerClient;
        this.stockCatalogService = stockCatalogService;
        this.redisTemplate = redisTemplate;
    }

    public BulkAnalysisResponse submitBulkFromWatchlist(AnalysisMode mode, int maxRetries) {
        throw legacyAnalysisRemoved();
    }

    public BulkAnalysisResponse submitBulkFromItems(List<? extends Map<String, ?>> items, AnalysisMode mode, int maxRetries) {
        List<BulkAnalysisResponse.BulkAnalysisFailure> failures = new ArrayList<>();

        for (Map<String, ?> entry : items) {
            String code = firstString(entry, "stockCode", "stock_code", "code");
            String name = firstString(entry, "stockName", "stock_name", "name");
            if (name == null || name.isBlank()) {
                name = code;
            }
            if (code == null || code.isBlank() || "null".equals(code)) {
                failures.add(new BulkAnalysisResponse.BulkAnalysisFailure(name, code,
                        "stock code missing"));
                continue;
            }
            failures.add(new BulkAnalysisResponse.BulkAnalysisFailure(name, code,
                    "legacy analysis flow removed"));
        }
        return new BulkAnalysisResponse(items.size(), 0, failures.size(), List.of(), failures);
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> extractWatchlist(Map<String, Object> status) {
        Object runtime = status.get("runtime");
        if (runtime instanceof Map<?, ?> runtimeMap) {
            Object wl = ((Map<String, Object>) runtimeMap).get("watchlist");
            if (wl instanceof List<?> list) {
                return (List<Map<String, Object>>) list;
            }
        }
        Object wl = status.get("watchlist");
        if (wl instanceof List<?> list) {
            return (List<Map<String, Object>>) list;
        }
        return List.of();
    }

    public AnalysisTaskResponse submit(AnalysisRequest request) {
        throw legacyAnalysisRemoved();
    }

    private ApiException legacyAnalysisRemoved() {
        return new ApiException(ErrorCode.ANALYSIS_FAILED, 410,
                "기존 단일 종목 분석 API는 제거되었습니다",
                "새 분석 파이프라인으로 대체 예정입니다");
    }

    public Map<String, Object> getProgress(String taskId) {
        throw legacyAnalysisRemoved();
    }

    public AnalysisResultResponse getResult(String taskId) {
        throw legacyAnalysisRemoved();
    }

    public SseEmitter stream(String taskId) {
        throw legacyAnalysisRemoved();
    }

    private void subscribeProgress(String taskId, SseEmitter emitter, AtomicBoolean done,
                                   AtomicReference<RedisMessageListenerContainer> progressContainer) {
        if (redisTemplate == null || redisTemplate.getConnectionFactory() == null) {
            return;
        }
        RedisConnectionFactory factory = redisTemplate.getConnectionFactory();
        RedisMessageListenerContainer container = new RedisMessageListenerContainer();
        try {
            container.setConnectionFactory(factory);
            MessageListener listener = (Message message, byte[] pattern) -> {
                if (done.get()) return;
                try {
                    Map<String, Object> event = parseProgressMessage(message);
                    if (event.isEmpty()) return;
                    Map<String, Object> normalized = normalizeProgressEvent(taskId, event);
                    recordProgressEvent(taskId, "progress", normalized);
                    emitter.send(SseEmitter.event().name("progress").data(normalized));
                    Map<String, Object> agentResult = agentResultFromProgress(normalized);
                    if (!agentResult.isEmpty()) {
                        recordProgressEvent(taskId, "agent_result", agentResult);
                        emitter.send(SseEmitter.event().name("agent_result").data(agentResult));
                    }
                } catch (Exception ignored) {
                    // The polling worker still owns final completion/error handling.
                }
            };
            container.addMessageListener(listener, new ChannelTopic("hqa:progress:" + taskId));
            container.afterPropertiesSet();
            container.start();
            progressContainer.set(container);
        } catch (Exception ignored) {
            stopRedis(progressContainer);
            // Redis progress is optional. Polling still returns final results.
        }
    }

    private Map<String, Object> parseProgressMessage(Message message) {
        try {
            String body = new String(message.getBody(), java.nio.charset.StandardCharsets.UTF_8);
            return new com.fasterxml.jackson.databind.ObjectMapper().readValue(body, new com.fasterxml.jackson.core.type.TypeReference<>() {});
        } catch (Exception e) {
            return Map.of();
        }
    }

    private void stopRedis(AtomicReference<RedisMessageListenerContainer> containerRef) {
        RedisMessageListenerContainer container = containerRef.getAndSet(null);
        if (container != null) {
            try {
                container.stop();
                container.destroy();
            } catch (Exception ignored) {
                // best effort
            }
        }
    }

    private void startProgressCapture(String taskId) {
        if (redisTemplate == null || redisTemplate.getConnectionFactory() == null) {
            return;
        }
        progressCaptureContainers.computeIfAbsent(taskId, ignored -> {
            RedisMessageListenerContainer container = new RedisMessageListenerContainer();
            container.setConnectionFactory(redisTemplate.getConnectionFactory());
            MessageListener listener = (message, pattern) -> {
                Map<String, Object> event = parseProgressMessage(message);
                if (event.isEmpty()) return;
                Map<String, Object> normalized = normalizeProgressEvent(taskId, event);
                recordProgressEvent(taskId, "progress", normalized);
                Map<String, Object> agentResult = agentResultFromProgress(normalized);
                if (!agentResult.isEmpty()) {
                    recordProgressEvent(taskId, "agent_result", agentResult);
                }
            };
            container.addMessageListener(listener, new ChannelTopic("hqa:progress:" + taskId));
            container.afterPropertiesSet();
            container.start();
            return container;
        });
    }

    private void stopProgressCapture(String taskId) {
        RedisMessageListenerContainer container = progressCaptureContainers.remove(taskId);
        if (container == null) return;
        try {
            container.stop();
            container.destroy();
        } catch (Exception ignored) {
            // best effort
        }
    }

    void recordProgressEvent(String taskId, String type, Map<String, Object> data) {
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("type", type);
        event.put("data", "progress".equals(type) ? normalizeProgressEvent(taskId, data) : data);
        List<Map<String, Object>> events = progressEvents.computeIfAbsent(
                taskId,
                ignored -> java.util.Collections.synchronizedList(new ArrayList<>())
        );
        synchronized (events) {
            if (!events.isEmpty() && events.get(events.size() - 1).equals(event)) {
                return;
            }
            events.add(event);
            if (events.size() > 120) {
                events.remove(0);
            }
        }
    }

    private Map<String, Object> normalizeProgressEvent(String taskId, Map<String, Object> data) {
        TaskMeta meta = tasks.get(taskId);
        if (meta == null) {
            return data;
        }

        String agent = stringOrNull(data.get("agent"));
        String status = stringOrNull(data.get("status"));
        if (agent == null || status == null) {
            return data;
        }

        double normalized = overallProgress(agent, status, meta.mode);
        double previous = latestProgress(taskId);
        Map<String, Object> next = new LinkedHashMap<>(data);
        next.put("progress", Math.max(previous, normalized));
        return next;
    }

    private double latestProgress(String taskId) {
        List<Map<String, Object>> events = progressEvents.get(taskId);
        if (events == null) {
            return 0.0;
        }
        synchronized (events) {
            for (int index = events.size() - 1; index >= 0; index--) {
                Map<String, Object> event = events.get(index);
                if (!"progress".equals(event.get("type"))) continue;
                Map<String, Object> data = castMap(event.get("data"));
                Object progress = data.get("progress");
                if (progress instanceof Number number) {
                    return number.doubleValue();
                }
            }
        }
        return 0.0;
    }

    private double overallProgress(String agent, String status, AnalysisMode mode) {
        if ("system".equals(agent) && "completed".equalsIgnoreCase(status)) {
            return 1.0;
        }
        if ("failed".equalsIgnoreCase(status) || "error".equalsIgnoreCase(status)) {
            return 0.95;
        }
        if (mode == AnalysisMode.quick) {
            return quickOverallProgress(agent, status);
        }
        return fullOverallProgress(agent, status);
    }

    private double quickOverallProgress(String agent, String status) {
        boolean completed = "completed".equalsIgnoreCase(status);
        return switch (agent) {
            case "system" -> 0.02;
            case "quant", "chartist" -> completed ? 0.45 : 0.12;
            case "quick_decision" -> completed ? 0.95 : 0.8;
            default -> completed ? 0.75 : 0.1;
        };
    }

    private double fullOverallProgress(String agent, String status) {
        boolean completed = "completed".equalsIgnoreCase(status);
        return switch (agent) {
            case "system" -> 0.02;
            case "analyst" -> completed ? 0.65 : 0.1;
            case "quant" -> completed ? 0.25 : 0.1;
            case "chartist" -> completed ? 0.5 : 0.1;
            case "quality_gate" -> completed ? 0.72 : 0.65;
            case "analyst_retry" -> completed ? 0.78 : 0.7;
            case "risk_manager" -> completed ? 0.95 : 0.82;
            default -> completed ? 0.75 : 0.1;
        };
    }

    List<Map<String, Object>> collectAgentResultEvents(Map<String, Object> aiData, Set<String> emittedAgents) {
        Map<String, Object> scores = castMap(aiData.get("scores"));
        if (scores.isEmpty()) return List.of();

        List<Map<String, Object>> events = new ArrayList<>();
        for (String agent : agentOrder()) {
            Map<String, Object> details = castMap(scores.get(agent));
            if (details.isEmpty() || emittedAgents.contains(agent)) continue;
            emittedAgents.add(agent);

            Map<String, Object> event = new LinkedHashMap<>();
            event.put("agent", agent);
            event.put("label", agentLabel(agent));
            event.put("status", "completed");
            event.put("message", agentSummary(agent, details));
            event.put("total_score", number(details.get("total_score")));
            event.put("grade", firstString(details, "grade", "signal", "action", "hegemony_grade"));
            event.put("opinion", firstString(details, "opinion", "summary", "final_opinion"));
            event.put("details", details);
            event.put("timestamp", OffsetDateTime.now(ZoneOffset.UTC).toString());
            events.add(event);
        }
        return events;
    }

    Map<String, Object> agentResultFromProgress(Map<String, Object> progress) {
        String agent = stringOrNull(progress.get("agent"));
        String status = stringOrNull(progress.get("status"));
        if (agent == null || agent.isBlank() || "system".equals(agent) || status == null || status.isBlank()) {
            return Map.of();
        }
        String message = stringOrNull(progress.get("message"));
        return agentProgressEvent(agent, status, message, String.valueOf(progress.getOrDefault(
                "timestamp", OffsetDateTime.now(ZoneOffset.UTC).toString())), progress);
    }

    private Map<String, Object> agentProgressEvent(String agent, String status, String message, String timestamp) {
        return agentProgressEvent(agent, status, message, timestamp, Map.of());
    }

    private Map<String, Object> agentProgressEvent(String agent, String status, String message, String timestamp,
                                                  Map<String, Object> details) {
        boolean completed = "completed".equalsIgnoreCase(status);
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("agent", agent);
        event.put("label", agentLabel(agent));
        event.put("status", status);
        event.put("message", agentLabel(agent) + " " + progressStatusLabel(status)
                + (message == null || message.isBlank() ? "" : ": " + message));
        event.put("total_score", completed ? 0.0 : null);
        event.put("grade", null);
        event.put("opinion", message);
        event.put("details", details);
        event.put("timestamp", timestamp);
        return event;
    }

    private String progressStatusLabel(String status) {
        if ("completed".equalsIgnoreCase(status)) return "완료";
        if ("failed".equalsIgnoreCase(status) || "error".equalsIgnoreCase(status)) return "실패";
        if ("started".equalsIgnoreCase(status) || "running".equalsIgnoreCase(status)) return "진행 중";
        return status;
    }

    private double progressValue(Map<String, Object> aiData, String status, AnalysisMode mode) {
        if ("completed".equalsIgnoreCase(status) || "failed".equalsIgnoreCase(status)) {
            return 1.0;
        }
        Map<String, Object> scores = castMap(aiData.get("scores"));
        int completed = 0;
        for (String agent : agentOrder(mode)) {
            if (!castMap(scores.get(agent)).isEmpty()) completed++;
        }
        return Math.max(0.08, Math.min(0.95, completed / (double) agentOrder(mode).size()));
    }

    private String progressMessage(Map<String, Object> aiData, String status, AnalysisMode mode) {
        if ("completed".equalsIgnoreCase(status)) return "분석이 완료되었습니다.";
        if ("failed".equalsIgnoreCase(status)) return "분석이 실패했습니다.";
        return agentLabel(nextAgentKey(aiData, status, mode)) + " 단계 진행 중";
    }

    private String nextAgentKey(Map<String, Object> aiData, String status, AnalysisMode mode) {
        if ("completed".equalsIgnoreCase(status) || "failed".equalsIgnoreCase(status)) {
            return "system";
        }
        Map<String, Object> scores = castMap(aiData.get("scores"));
        for (String agent : agentOrder(mode)) {
            if (castMap(scores.get(agent)).isEmpty()) {
                return agent;
            }
        }
        return mode == AnalysisMode.quick ? "quick_decision" : "risk_manager";
    }

    private List<String> agentOrder() {
        return List.of("analyst", "quant", "chartist", "risk_manager", "quick_decision");
    }

    private List<String> agentOrder(AnalysisMode mode) {
        if (mode == AnalysisMode.quick) {
            return List.of("quant", "chartist", "quick_decision");
        }
        return List.of("analyst", "quant", "chartist", "risk_manager");
    }

    private String agentLabel(String agent) {
        return switch (agent) {
            case "analyst" -> "Analyst";
            case "quant" -> "Quant";
            case "chartist" -> "Chartist";
            case "quality_gate" -> "Quality Gate";
            case "analyst_retry" -> "Analyst Retry";
            case "risk_manager" -> "Risk Manager";
            case "quick_decision" -> "Quick Decision";
            default -> agent;
        };
    }

    private String agentSummary(String agent, Map<String, Object> details) {
        String label = agentLabel(agent);
        double score = number(details.get("total_score"));
        String grade = firstString(details, "grade", "signal", "action", "hegemony_grade");
        String opinion = firstString(details, "opinion", "summary", "final_opinion");
        List<String> parts = new ArrayList<>();
        if (score > 0) parts.add("점수 " + Math.round(score));
        if (grade != null) parts.add(grade);
        if (opinion != null) parts.add(opinion);
        return parts.isEmpty()
                ? label + " 결과가 도착했습니다."
                : label + " 완료: " + String.join(" · ", parts);
    }

    public QuerySuggestionResponse suggest(QuerySuggestionRequest request) {
        Map<String, Object> response = aiServerClient.suggest(Map.of("query", request.getQuery()));
        return new QuerySuggestionResponse(
                String.valueOf(response.getOrDefault("original_query", request.getQuery())),
                Boolean.TRUE.equals(response.getOrDefault("is_answerable", true)),
                stringOrNull(response.get("corrected_query")),
                castStringList(response.get("suggestions")),
                stringOrNull(response.get("reason"))
        );
    }

    public AnalysisHistoryResponse getHistory(int page, int pageSize) {
        List<TaskMeta> all = tasks.values().stream().toList();
        int from = Math.min((page - 1) * pageSize, all.size());
        int to = Math.min(from + pageSize, all.size());
        List<AnalysisHistoryItem> items = all.subList(from, to).stream()
                .map(meta -> new AnalysisHistoryItem(meta.taskId,
                        new StockInfo(meta.stockName, meta.stockCode),
                        meta.mode,
                        meta.status,
                        meta.totalScore,
                        meta.action,
                        meta.createdAt,
                        meta.completedAt))
                .collect(Collectors.toList());
        return new AnalysisHistoryResponse(items, all.size(), page, pageSize);
    }

    private AnalysisResultResponse toResult(TaskMeta meta, Map<String, Object> aiData) {
        Map<String, Object> scores = castMap(aiData.get("scores"));
        List<ScoreDetail> scoreDetails = new ArrayList<>();
        if (scores.containsKey("analyst") && !castMap(scores.get("analyst")).isEmpty()) {
            Map<String, Object> analyst = castMap(scores.get("analyst"));
            scoreDetails.add(new ScoreDetail("analyst",
                    number(analyst.get("total_score")),
                    70.0,
                    stringOrNull(analyst.get("hegemony_grade")),
                    stringOrNull(analyst.get("final_opinion")),
                    analyst));
        }
        if (scores.containsKey("quant") && !castMap(scores.get("quant")).isEmpty()) {
            Map<String, Object> quant = castMap(scores.get("quant"));
            scoreDetails.add(new ScoreDetail("quant",
                    number(quant.get("total_score")),
                    100.0,
                    stringOrNull(quant.get("grade")),
                    stringOrNull(quant.get("opinion")),
                    quant));
        }
        if (scores.containsKey("chartist") && !castMap(scores.get("chartist")).isEmpty()) {
            Map<String, Object> chartist = castMap(scores.get("chartist"));
            scoreDetails.add(new ScoreDetail("chartist",
                    number(chartist.get("total_score")),
                    100.0,
                    stringOrNull(chartist.get("signal")),
                    null,
                    chartist));
        }
        if (scores.containsKey("risk_manager") && !castMap(scores.get("risk_manager")).isEmpty()) {
            Map<String, Object> risk = castMap(scores.get("risk_manager"));
            scoreDetails.add(new ScoreDetail("risk_manager",
                    number(risk.get("total_score")),
                    100.0,
                    firstString(risk, "grade", "action"),
                    firstString(risk, "opinion", "summary"),
                    risk));
        }
        if (scores.containsKey("quick_decision") && !castMap(scores.get("quick_decision")).isEmpty()) {
            Map<String, Object> quick = castMap(scores.get("quick_decision"));
            scoreDetails.add(new ScoreDetail("quick_decision",
                    number(quick.get("total_score")),
                    100.0,
                    firstString(quick, "grade", "action"),
                    firstString(quick, "opinion", "summary"),
                    quick));
        }

        OffsetDateTime completedAt = parseCompletedAt(aiData.get("completed_at"));
        Double duration = completedAt == null ? null : (double) Duration.between(meta.createdAt, completedAt).toSeconds();
        return new AnalysisResultResponse(
                meta.taskId,
                meta.status,
                stockCatalogService.getStockInfo(meta.stockCode),
                meta.mode,
                scoreDetails,
                castMap(aiData.get("final_decision")),
                stringOrNull(aiData.get("research_quality")),
                castStringList(aiData.get("quality_warnings")),
                meta.createdAt,
                completedAt,
                duration,
                castStringMap(aiData.get("errors"))
        );
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> castMap(Object value) {
        return value instanceof Map<?, ?> map ? (Map<String, Object>) map : Map.of();
    }

    @SuppressWarnings("unchecked")
    private Map<String, String> castStringMap(Object value) {
        return value instanceof Map<?, ?> map ? (Map<String, String>) map : Map.of();
    }

    @SuppressWarnings("unchecked")
    private List<String> castStringList(Object value) {
        return value instanceof List<?> list ? (List<String>) list : List.of();
    }

    private String stringOrNull(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private String firstString(Map<String, ?> map, String... keys) {
        for (String key : keys) {
            Object value = map.get(key);
            if (value != null && !String.valueOf(value).isBlank() && !"null".equals(String.valueOf(value))) {
                return String.valueOf(value);
            }
        }
        return null;
    }

    private double number(Object value) {
        return value instanceof Number number ? number.doubleValue() : 0.0;
    }

    private OffsetDateTime parseCompletedAt(Object value) {
        if (value == null) {
            return null;
        }
        String raw = String.valueOf(value);
        try {
            return OffsetDateTime.parse(raw);
        } catch (Exception ignored) {
            try {
                return LocalDateTime.parse(raw).atZone(ZoneId.systemDefault()).toOffsetDateTime();
            } catch (Exception alsoIgnored) {
                return null;
            }
        }
    }

    private void updateMetaFromAiData(TaskMeta meta, Map<String, Object> aiData) {
        OffsetDateTime completedAt = parseCompletedAt(aiData.get("completed_at"));
        if (completedAt != null) {
            meta.completedAt = completedAt;
        }
        Map<String, Object> decision = castMap(aiData.get("final_decision"));
        if (!decision.isEmpty()) {
            Object score = decision.get("total_score");
            if (score instanceof Number number) {
                meta.totalScore = number.doubleValue();
            }
            meta.action = firstString(decision, "action", "action_code");
        }
    }

    private static class TaskMeta {
        private final String taskId;
        private final String stockName;
        private final String stockCode;
        private final AnalysisMode mode;
        private final int maxRetries;
        private final OffsetDateTime createdAt = OffsetDateTime.now();
        private AnalysisStatus status = AnalysisStatus.pending;
        private Double totalScore;
        private String action;
        private OffsetDateTime completedAt;

        private TaskMeta(String taskId, String stockName, String stockCode, AnalysisMode mode, int maxRetries) {
            this.taskId = taskId;
            this.stockName = stockName;
            this.stockCode = stockCode;
            this.mode = mode;
            this.maxRetries = maxRetries;
        }
    }
}
