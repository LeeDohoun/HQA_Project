package com.hqa.backend.dto;

import java.util.List;

public record AnalysisHistoryResponse(
        List<AnalysisHistoryItem> items,
        int total,
        int page,
        int pageSize
) {
}
