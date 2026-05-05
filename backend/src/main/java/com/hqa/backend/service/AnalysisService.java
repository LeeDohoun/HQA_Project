package com.hqa.backend.service;

import com.hqa.backend.dto.*;
import com.hqa.backend.exception.ApiException;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Service
public class AnalysisService {

    private final AiServerClient aiServerClient;
    private final StockCatalogService stockCatalogService;
    private final Map<String, TaskMeta> tasks = new LinkedHashMap<>();
    private final Map<String, List<SseEmitter>> emitters = new ConcurrentHashMap<>();

    public AnalysisService(AiServerClient aiServerClient, StockCatalogService stockCatalogService) {
        this.aiServerClient = aiServerClient;
        this.stockCatalogService = stockCatalogService;
    }

    public BulkAnalysisResponse submitBulkFromWatchlist(AnalysisMode mode, int maxRetries) {
        Map<String, Object> tradingStatus;
        try {
            tradingStatus = aiServerClient.getTradingStatus();
        } catch (Exception e) {
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503,
                    "AI 서버에서 워치리스트를 가져오지 못했습니다", e.getMessage());
        }

        List<Map<String, Object>> watchlist = extractWatchlist(tradingStatus);
        List<AnalysisTaskResponse> submitted = new ArrayList<>();
        List<BulkAnalysisResponse.BulkAnalysisFailure> failures = new ArrayList<>();

        for (Map<String, Object> entry : watchlist) {
            String code = String.valueOf(entry.getOrDefault("code", entry.get("stock_code")));
            String name = String.valueOf(entry.getOrDefault("name",
                    entry.getOrDefault("stock_name", code)));
            if (code == null || code.isBlank() || "null".equals(code)) {
                failures.add(new BulkAnalysisResponse.BulkAnalysisFailure(name, code,
                        "stock code missing"));
                continue;
            }
            AnalysisRequest req = new AnalysisRequest();
            req.setStockName(name);
            req.setStockCode(code);
            req.setMode(mode);
            req.setMaxRetries(maxRetries);
            try {
                submitted.add(submit(req));
            } catch (Exception e) {
                failures.add(new BulkAnalysisResponse.BulkAnalysisFailure(name, code,
                        e.getMessage()));
            }
        }
        return new BulkAnalysisResponse(
                watchlist.size(), submitted.size(), failures.size(), submitted, failures);
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
        String taskId = UUID.randomUUID().toString();
        TaskMeta meta = new TaskMeta(taskId, request.getStockName(), request.getStockCode(), request.getMode(), request.getMaxRetries());
        tasks.put(taskId, meta);
        aiServerClient.submitAnalysis(Map.of(
                "task_id", taskId,
                "stock_name", request.getStockName(),
                "stock_code", request.getStockCode(),
                "mode", request.getMode().name(),
                "max_retries", request.getMaxRetries()
        ));
        meta.status = AnalysisStatus.running;
        return new AnalysisTaskResponse(taskId, AnalysisStatus.pending,
                request.getStockName() + "(" + request.getStockCode() + ") analysis queued",
                request.getMode() == AnalysisMode.full ? 180 : 60);
    }

    public AnalysisResultResponse getResult(String taskId) {
        TaskMeta meta = tasks.get(taskId);
        if (meta == null) {
            throw new ApiException(ErrorCode.ANALYSIS_NOT_FOUND, 404, "Analysis not found", taskId);
        }
        Map<String, Object> aiData = aiServerClient.getAnalysis(taskId);
        String status = String.valueOf(aiData.getOrDefault("status", meta.status.name()));
        if ("completed".equalsIgnoreCase(status)) {
            meta.status = AnalysisStatus.completed;
        } else if ("failed".equalsIgnoreCase(status)) {
            meta.status = AnalysisStatus.failed;
        }
        return toResult(meta, aiData);
    }

    public SseEmitter stream(String taskId) {
        if (!tasks.containsKey(taskId)) {
            throw new ApiException(ErrorCode.ANALYSIS_NOT_FOUND, 404, "Analysis not found", taskId);
        }
        SseEmitter emitter = new SseEmitter(600_000L);
        emitters.computeIfAbsent(taskId, ignored -> new ArrayList<>()).add(emitter);

        Thread worker = new Thread(() -> {
            try {
                while (true) {
                    Map<String, Object> data = aiServerClient.getAnalysis(taskId);
                    String status = String.valueOf(data.getOrDefault("status", "running"));
                    emitter.send(SseEmitter.event()
                            .name("progress")
                            .data(Map.of(
                                    "agent", "system",
                                    "status", status,
                                    "message", "Analysis in progress",
                                    "progress", "completed".equals(status) ? 1.0 : 0.5,
                                    "timestamp", OffsetDateTime.now(ZoneOffset.UTC).toString()
                            )));
                    if ("completed".equals(status) || "failed".equals(status)) {
                        emitter.send(SseEmitter.event().name("completed").data(Map.of("task_id", taskId, "status", status)));
                        emitter.complete();
                        break;
                    }
                    Thread.sleep(2000L);
                }
            } catch (Exception exception) {
                emitter.completeWithError(exception);
            }
        });
        worker.setDaemon(true);
        worker.start();
        return emitter;
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
                        null,
                        null,
                        meta.createdAt,
                        null))
                .collect(Collectors.toList());
        return new AnalysisHistoryResponse(items, all.size(), page, pageSize);
    }

    private AnalysisResultResponse toResult(TaskMeta meta, Map<String, Object> aiData) {
        Map<String, Object> scores = castMap(aiData.get("scores"));
        List<ScoreDetail> scoreDetails = new ArrayList<>();
        if (scores.containsKey("analyst")) {
            Map<String, Object> analyst = castMap(scores.get("analyst"));
            scoreDetails.add(new ScoreDetail("analyst",
                    number(analyst.get("total_score")),
                    70.0,
                    stringOrNull(analyst.get("hegemony_grade")),
                    stringOrNull(analyst.get("final_opinion")),
                    analyst));
        }
        if (scores.containsKey("quant")) {
            Map<String, Object> quant = castMap(scores.get("quant"));
            scoreDetails.add(new ScoreDetail("quant",
                    number(quant.get("total_score")),
                    100.0,
                    stringOrNull(quant.get("grade")),
                    stringOrNull(quant.get("opinion")),
                    quant));
        }
        if (scores.containsKey("chartist")) {
            Map<String, Object> chartist = castMap(scores.get("chartist"));
            scoreDetails.add(new ScoreDetail("chartist",
                    number(chartist.get("total_score")),
                    100.0,
                    stringOrNull(chartist.get("signal")),
                    null,
                    chartist));
        }

        OffsetDateTime completedAt = null;
        if (aiData.containsKey("completed_at")) {
            try {
                completedAt = OffsetDateTime.parse(String.valueOf(aiData.get("completed_at")));
            } catch (Exception ignored) {
                completedAt = null;
            }
        }
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

    private double number(Object value) {
        return value instanceof Number number ? number.doubleValue() : 0.0;
    }

    private static class TaskMeta {
        private final String taskId;
        private final String stockName;
        private final String stockCode;
        private final AnalysisMode mode;
        private final int maxRetries;
        private final OffsetDateTime createdAt = OffsetDateTime.now();
        private AnalysisStatus status = AnalysisStatus.pending;

        private TaskMeta(String taskId, String stockName, String stockCode, AnalysisMode mode, int maxRetries) {
            this.taskId = taskId;
            this.stockName = stockName;
            this.stockCode = stockCode;
            this.mode = mode;
            this.maxRetries = maxRetries;
        }
    }
}
