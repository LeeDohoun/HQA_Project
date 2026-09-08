package com.hqa.backend.dto;

public record AnalysisTaskResponse(
        String taskId,
        AnalysisStatus status,
        String message,
        Integer estimatedTimeSeconds
) {
}
