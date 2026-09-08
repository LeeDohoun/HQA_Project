package com.hqa.backend.controller;

import com.hqa.backend.dto.*;
import com.hqa.backend.service.AnalysisService;
import com.hqa.backend.service.AuthService;
import jakarta.servlet.http.HttpSession;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

@Tag(name = "AI 분석", description = "종목 AI 분석 작업 제출·조회·이력 (로그인 필요)")
@Validated
@RestController
@RequestMapping("/api/v1/analysis")
public class AnalysisController {

    private final AnalysisService analysisService;

    private final AuthService authService;

    public AnalysisController(AnalysisService analysisService, AuthService authService) {
        this.authService = authService;
        this.analysisService = analysisService;
    }

    @Operation(summary = "단일 종목 분석 제출",
            description = "단일 종목에 대한 AI 분석 작업을 비동기로 제출한다. 응답으로 받은 task_id로 결과를 조회한다. (202 Accepted)")
    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    public AnalysisTaskResponse create(@Valid @RequestBody AnalysisRequest request, HttpSession session) {
        return analysisService.submit(request, authService.requireUser(session));
    }

    @Operation(summary = "다종목 일괄 분석 제출",
            description = "여러 종목을 한 번에 분석 제출한다. 본문을 비우면 사용자의 워치리스트 기반으로 제출한다. (202 Accepted)")
    @PostMapping("/bulk")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public BulkAnalysisResponse createBulk(
            @RequestParam(defaultValue = "full") AnalysisMode mode,
            @RequestParam(defaultValue = "0") int maxRetries,
            @Valid @RequestBody(required = false) BulkAnalysisRequest request, HttpSession session) {
        var user = authService.requireUser(session);
        if (request != null) {
            var items = request.getItems().stream()
                    .map(item -> {
                        java.util.Map<String, Object> row = new java.util.LinkedHashMap<>();
                        row.put("stockName", item.getStockName());
                        row.put("stockCode", item.getStockCode());
                        return row;
                    })
                    .toList();
            return analysisService.submitBulkFromItems(items, mode, maxRetries, user);
        }
        return analysisService.submitBulkFromWatchlist(mode, maxRetries, user);
    }

    @Operation(summary = "분석 결과 조회",
            description = "task_id로 본인의 공통 종목 분석 상태와 세 전문가의 결과를 조회한다. 계좌별 매매 판단은 포함하지 않는다.")
    @GetMapping("/{taskId}")
    public AnalysisResultResponse getResult(@PathVariable String taskId, HttpSession session) {
        return analysisService.getResult(taskId, authService.requireUser(session));
    }

    @Operation(summary = "분석 진행 상황 조회",
            description = "본인의 분석 작업 상태를 조회한다. 진행 중에는 주기적으로 다시 조회한다.")
    @GetMapping("/{taskId}/progress")
    public Map<String, Object> getProgress(@PathVariable String taskId, HttpSession session) {
        return analysisService.getProgress(taskId, authService.requireUser(session));
    }

    @Operation(summary = "검색어 보정/제안",
            description = "사용자 입력 검색어가 분석 가능한지 판단하고, 보정된 검색어와 대안 제안을 반환한다.")
    @PostMapping("/suggest")
    public QuerySuggestionResponse suggest(@Valid @RequestBody QuerySuggestionRequest request, HttpSession session) {
        authService.requireUser(session);
        return analysisService.suggest(request);
    }

    @Operation(summary = "분석 이력 목록",
            description = "사용자의 과거 분석 이력을 페이지네이션으로 조회한다.")
    @GetMapping("/history/list")
    public AnalysisHistoryResponse history(@RequestParam(defaultValue = "1") int page,
                                           @RequestParam(defaultValue = "20") int pageSize, HttpSession session) {
        return analysisService.getHistory(page, pageSize, authService.requireUser(session));
    }
}
