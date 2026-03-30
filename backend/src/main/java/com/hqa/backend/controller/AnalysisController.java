package com.hqa.backend.controller;

import com.hqa.backend.dto.*;
import com.hqa.backend.service.AnalysisService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Validated
@RestController
@RequestMapping("/api/v1/analysis")
public class AnalysisController {

    private final AnalysisService analysisService;

    public AnalysisController(AnalysisService analysisService) {
        this.analysisService = analysisService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    public AnalysisTaskResponse create(@Valid @RequestBody AnalysisRequest request) {
        return analysisService.submit(request);
    }

    @GetMapping("/{taskId}")
    public AnalysisResultResponse getResult(@PathVariable String taskId) {
        return analysisService.getResult(taskId);
    }

    @GetMapping(path = "/{taskId}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@PathVariable String taskId) {
        return analysisService.stream(taskId);
    }

    @PostMapping("/suggest")
    public QuerySuggestionResponse suggest(@Valid @RequestBody QuerySuggestionRequest request) {
        return analysisService.suggest(request);
    }

    @GetMapping("/history/list")
    public AnalysisHistoryResponse history(@RequestParam(defaultValue = "1") int page,
                                           @RequestParam(defaultValue = "20") int pageSize) {
        return analysisService.getHistory(page, pageSize);
    }
}
