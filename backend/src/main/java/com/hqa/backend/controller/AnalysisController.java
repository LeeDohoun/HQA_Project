package com.hqa.backend.controller;

import com.hqa.backend.dto.*;
import com.hqa.backend.service.AnalysisService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Tag(name = "AI 분석", description = "종목 AI 분석 작업 제출·조회·이력 (로그인 필요)")
@Validated
@RestController
@RequestMapping("/api/v1/analysis")
public class AnalysisController {

    private final AnalysisService analysisService;

    public AnalysisController(AnalysisService analysisService) {
        this.analysisService = analysisService;
    }

    @Operation(summary = "단일 종목 분석 제출",
            description = "단일 종목에 대한 AI 분석 작업을 비동기로 제출한다. 응답으로 받은 task_id로 결과를 조회한다. (202 Accepted)")
    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    public AnalysisTaskResponse create(@Valid @RequestBody AnalysisRequest request) {
        return analysisService.submit(request);
    }

    @Operation(summary = "다종목 일괄 분석 제출",
            description = "여러 종목을 한 번에 분석 제출한다. 본문을 비우면 사용자의 워치리스트 기반으로 제출한다. (202 Accepted)")
    @PostMapping("/bulk")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public BulkAnalysisResponse createBulk(
            @RequestParam(defaultValue = "quick") AnalysisMode mode,
            @RequestParam(defaultValue = "0") int maxRetries,
            @RequestBody(required = false) BulkAnalysisRequest request) {
        if (request != null && request.getItems() != null && !request.getItems().isEmpty()) {
            var items = request.getItems().stream()
                    .map(item -> {
                        java.util.Map<String, Object> row = new java.util.LinkedHashMap<>();
                        row.put("stockName", item.getStockName());
                        row.put("stockCode", item.getStockCode());
                        return row;
                    })
                    .toList();
            return analysisService.submitBulkFromItems(items, mode, Math.max(0, Math.min(3, maxRetries)));
        }
        return analysisService.submitBulkFromWatchlist(mode, Math.max(0, Math.min(3, maxRetries)));
    }

    @Operation(summary = "분석 결과 조회",
            description = "task_id로 분석 작업의 상태와 결과(에이전트별 점수, 최종 판단 등)를 조회한다.")
    @GetMapping("/{taskId}")
    public AnalysisResultResponse getResult(@PathVariable String taskId) {
        return analysisService.getResult(taskId);
    }

    @Operation(summary = "분석 진행 상황 조회",
            description = "SSE가 불안정한 네트워크에서도 사용할 수 있도록 저장된 진행 이벤트를 조회한다.")
    @GetMapping("/{taskId}/progress")
    public Map<String, Object> getProgress(@PathVariable String taskId) {
        return analysisService.getProgress(taskId);
    }

    @Operation(summary = "분석 진행 상황 SSE 스트림",
            description = "분석 작업의 진행 상황을 Server-Sent Events(text/event-stream)로 실시간 스트리밍한다.")
    @GetMapping(path = "/{taskId}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@PathVariable String taskId) {
        return analysisService.stream(taskId);
    }

    @Operation(summary = "검색어 보정/제안",
            description = "사용자 입력 검색어가 분석 가능한지 판단하고, 보정된 검색어와 대안 제안을 반환한다.")
    @PostMapping("/suggest")
    public QuerySuggestionResponse suggest(@Valid @RequestBody QuerySuggestionRequest request) {
        return analysisService.suggest(request);
    }

    @Operation(summary = "분석 이력 목록",
            description = "사용자의 과거 분석 이력을 페이지네이션으로 조회한다.")
    @GetMapping("/history/list")
    public AnalysisHistoryResponse history(@RequestParam(defaultValue = "1") int page,
                                           @RequestParam(defaultValue = "20") int pageSize) {
        return analysisService.getHistory(page, pageSize);
    }
}
