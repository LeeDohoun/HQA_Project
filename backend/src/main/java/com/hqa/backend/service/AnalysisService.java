package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.*;
import com.hqa.backend.entity.AnalysisRecord;
import com.hqa.backend.entity.User;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.AnalysisRecordRepository;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

/** Adapts the current company-analysis runtime to authenticated dashboard tasks. */
@Service
public class AnalysisService {
    private final AiServerClient ai;
    private final AnalysisRecordRepository records;
    private final WatchlistService watchlist;
    private final ObjectMapper mapper;

    public AnalysisService(AiServerClient ai, AnalysisRecordRepository records, WatchlistService watchlist, ObjectMapper mapper) {
        this.ai = ai;
        this.records = records;
        this.watchlist = watchlist;
        this.mapper = mapper;
    }

    public AnalysisTaskResponse submit(AnalysisRequest request, User user) {
        requireMode(request.getMode(), request.getMaxRetries());
        String code = request.getStockCode();
        if (code == null || !code.matches("[0-9]{6}") || request.getStockName() == null || request.getStockName().isBlank()) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "종목 이름과 6자리 코드가 필요합니다", null);
        }
        AnalysisRecord record = new AnalysisRecord();
        Map<String, Object> task = ai.submitStockPreview(code);
        String taskId = UUID.fromString(String.valueOf(task.get("task_id"))).toString();
        record.setUser(user);
        record.setTaskId(taskId);
        record.setStockCode(code);
        record.setStockName(request.getStockName().trim());
        record.setMode("full");
        record.setMaxRetries(0);
        record.setStatus("pending");
        records.save(record);
        return new AnalysisTaskResponse(taskId, AnalysisStatus.pending, record.getStockName() + " 공통 종목 분석을 시작했습니다", null);
    }

    public BulkAnalysisResponse submitBulkFromWatchlist(AnalysisMode mode, int maxRetries, User user) {
        var items = watchlist.list(user).items().stream()
                .map(item -> Map.of("stockName", item.stockName(), "stockCode", item.stockCode())).toList();
        return submitBulkFromItems(items, mode, maxRetries, user);
    }

    public BulkAnalysisResponse submitBulkFromItems(List<? extends Map<String, ?>> items,
            AnalysisMode mode, int maxRetries, User user) {
        requireMode(mode, maxRetries);
        if (items.size() > 20) throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "한 번에 최대 20개 종목을 분석할 수 있습니다", null);
        List<AnalysisTaskResponse> tasks = new ArrayList<>();
        List<BulkAnalysisResponse.BulkAnalysisFailure> failures = new ArrayList<>();
        for (Map<String, ?> item : items) {
            String name = (String) item.get("stockName");
            String code = (String) item.get("stockCode");
            AnalysisRequest request = new AnalysisRequest();
            request.setStockName(name);
            request.setStockCode(code);
            try {
                tasks.add(submit(request, user));
            } catch (RuntimeException ex) {
                failures.add(new BulkAnalysisResponse.BulkAnalysisFailure(name, code, ex.getMessage()));
            }
        }
        return new BulkAnalysisResponse(items.size(), tasks.size(), failures.size(), tasks, failures);
    }

    public AnalysisResultResponse getResult(String taskId, User user) {
        AnalysisRecord record = findRecord(taskId, user);
        if (record.getResultJson() != null) return decode(record.getResultJson());
        Map<String, Object> task;
        try {
            task = ai.getRuntimeTask(taskId);
        } catch (ApiException ex) {
            if (ex.getStatus() != 404) throw ex;
            task = Map.of("task_id", taskId, "status", "failed", "failed_at", OffsetDateTime.now().toString(),
                    "error", "AI 작업이 만료되었거나 서버 재시작으로 소실되었습니다");
        }
        if (!taskId.equals(task.get("task_id"))) throw new IllegalStateException("AI_TASK_ID_MISMATCH");
        String state = String.valueOf(task.get("status"));
        if (state.equals("queued") || state.equals("running")) {
            if (state.equals("running")) records.markRunning(taskId, user.getId());
            AnalysisRecord current = findRecord(taskId, user);
            return current.getResultJson() == null ? response(current, List.of(), List.of(), Map.of())
                    : decode(current.getResultJson());
        }
        if (!state.equals("completed") && !state.equals("failed")) throw new IllegalStateException("AI_TASK_STATUS_INVALID");
        OffsetDateTime completedAt = OffsetDateTime.parse((String) task.get(state.equals("failed") ? "failed_at" : "completed_at"));
        List<ScoreDetail> scores = new ArrayList<>();
        List<String> warnings = new ArrayList<>();
        Map<String, String> errors;
        if (state.equals("failed")) {
            errors = Map.of("runtime", String.valueOf(task.get("error")));
        } else {
            Map<String, Object> result = mapper.convertValue(task.get("result"), new TypeReference<>() { });
            if (!record.getStockCode().equals(result.get("stock_code"))) throw new IllegalStateException("AI_STOCK_MISMATCH");
            state = String.valueOf(result.get("status"));
            if (!List.of("completed", "failed").contains(state)) throw new IllegalStateException("AI_RESULT_STATUS_INVALID");
            record.setStockName(String.valueOf(result.get("stock_name")));
            Map<String, Map<String, Object>> specialists = mapper.convertValue(result.get("specialists"), new TypeReference<>() { });
            if (state.equals("completed") && !specialists.keySet().equals(java.util.Set.of("analyst", "quant", "chartist"))) {
                throw new IllegalStateException("AI_SPECIALISTS_INCOMPLETE");
            }
            for (String role : List.of("analyst", "quant", "chartist")) {
                Map<String, Object> row = specialists.get(role);
                if (row == null) continue;
                if (!role.equals(row.get("role")) || !record.getStockCode().equals(row.get("stock_code"))) {
                    throw new IllegalStateException("AI_SPECIALIST_IDENTITY_MISMATCH");
                }
                double score = ((Number) row.get("score")).doubleValue();
                if (!Double.isFinite(score) || score < 0 || score > 100) throw new IllegalStateException("AI_SCORE_INVALID");
                scores.add(new ScoreDetail(role, score, 100, null, (String) row.get("thesis"), row));
            }
            warnings.addAll(mapper.convertValue(result.get("data_gaps"), new TypeReference<List<String>>() { }));
            errors = mapper.convertValue(result.get("errors"), new TypeReference<>() { });
        }
        record.setStatus(state);
        record.setCompletedAt(completedAt);
        warnings.add("계좌별 매매 판단과 주문을 포함하지 않는 공통 종목 분석입니다.");
        AnalysisResultResponse response = response(record, scores, warnings, errors);
        try { record.setResultJson(mapper.writeValueAsString(response)); }
        catch (com.fasterxml.jackson.core.JsonProcessingException ex) { throw new IllegalStateException("ANALYSIS_SERIALIZATION_FAILED", ex); }
        records.storeResultIfAbsent(taskId, user.getId(), record.getStatus(), record.getStockName(),
                record.getCompletedAt(), record.getResultJson());
        return decode(findRecord(taskId, user).getResultJson());
    }

    public Map<String, Object> getProgress(String taskId, User user) {
        AnalysisResultResponse result = getResult(taskId, user);
        boolean complete = result.status() == AnalysisStatus.completed || result.status() == AnalysisStatus.failed;
        Map<String, Object> event = Map.of("agent", "analysis", "status", result.status().name(),
                "message", complete ? "분석 작업이 종료되었습니다" : "공통 종목 분석을 진행 중입니다", "progress", complete ? 1.0 : 0.0,
                "timestamp", (complete ? result.completedAt() : result.createdAt()).toString());
        return Map.of("task_id", taskId, "status", result.status(), "events", List.of(Map.of("type", "progress", "data", event)));
    }

    public AnalysisHistoryResponse getHistory(int page, int pageSize, User user) {
        if (page < 1 || pageSize < 1 || pageSize > 100) throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "잘못된 페이지 범위입니다", null);
        var history = records.findByUser_IdOrderByCreatedAtDesc(user.getId(), PageRequest.of(page - 1, pageSize));
        List<AnalysisHistoryItem> items = history.getContent().stream().map(record -> {
            AnalysisResultResponse result = record.getResultJson() == null
                    ? response(record, List.of(), List.of(), Map.of()) : decode(record.getResultJson());
            Double score = result.scores().size() == 3 ? result.scores().stream().mapToDouble(ScoreDetail::totalScore).average().orElseThrow() : null;
            return new AnalysisHistoryItem(result.taskId(), result.stock(), result.mode(), result.status(), score,
                    null, result.createdAt(), result.completedAt());
        }).toList();
        return new AnalysisHistoryResponse(items, Math.toIntExact(history.getTotalElements()), page, pageSize);
    }

    public QuerySuggestionResponse suggest(QuerySuggestionRequest request) {
        Map<String, Object> result = ai.suggest(Map.of("query", request.getQuery()));
        return mapper.convertValue(result, QuerySuggestionResponse.class);
    }

    private AnalysisResultResponse response(AnalysisRecord record, List<ScoreDetail> scores,
            List<String> warnings, Map<String, String> errors) {
        return new AnalysisResultResponse(record.getTaskId(), AnalysisStatus.valueOf(record.getStatus()),
                new StockInfo(record.getStockName(), record.getStockCode()), AnalysisMode.full, scores, null,
                null, warnings, record.getCreatedAt(), record.getCompletedAt(), record.getCompletedAt() == null ? null
                    : Duration.between(record.getCreatedAt(), record.getCompletedAt()).toMillis() / 1000.0, errors);
    }

    private AnalysisResultResponse decode(String json) {
        try { return mapper.readValue(json, AnalysisResultResponse.class); }
        catch (java.io.IOException ex) { throw new IllegalStateException("INVALID_STORED_ANALYSIS", ex); }
    }

    private AnalysisRecord findRecord(String taskId, User user) {
        return records.findByTaskIdAndUser_Id(taskId, user.getId())
                .orElseThrow(() -> new ApiException(ErrorCode.ANALYSIS_NOT_FOUND, 404, "분석 작업을 찾지 못했습니다", null));
    }

    private static void requireMode(AnalysisMode mode, int retries) {
        if (mode != AnalysisMode.full || retries != 0) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "현재 엔진은 재시도 없는 공통 종목 분석(full)만 지원합니다", null);
        }
    }
}
