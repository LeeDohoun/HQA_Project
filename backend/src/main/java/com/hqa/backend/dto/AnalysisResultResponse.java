package com.hqa.backend.dto;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

public record AnalysisResultResponse(
        String taskId,
        AnalysisStatus status,
        StockInfo stock,
        AnalysisMode mode,
        List<ScoreDetail> scores,
        Map<String, Object> finalDecision,
        String researchQuality,
        List<String> qualityWarnings,
        OffsetDateTime createdAt,
        OffsetDateTime completedAt,
        Double durationSeconds,
        Map<String, String> errors
) {
}
