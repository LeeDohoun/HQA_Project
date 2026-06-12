package com.hqa.backend.dto;

import java.time.OffsetDateTime;

public record WatchlistItemResponse(
        String id,
        String stockName,
        String stockCode,
        String market,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {
}
