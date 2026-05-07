package com.hqa.backend.dto;

import java.time.OffsetDateTime;

public record AnalysisHistoryItem(
        String taskId,
        StockInfo stock,
        AnalysisMode mode,
        AnalysisStatus status,
        Double totalScore,
        String action,
        OffsetDateTime createdAt,
        OffsetDateTime completedAt
) {
}
