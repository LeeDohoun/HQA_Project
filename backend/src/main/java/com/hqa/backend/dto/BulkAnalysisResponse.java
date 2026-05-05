package com.hqa.backend.dto;

import java.util.List;

public record BulkAnalysisResponse(
        int total,
        int submitted,
        int failed,
        List<AnalysisTaskResponse> tasks,
        List<BulkAnalysisFailure> failures
) {
    public record BulkAnalysisFailure(String stockName, String stockCode, String reason) {}
}
