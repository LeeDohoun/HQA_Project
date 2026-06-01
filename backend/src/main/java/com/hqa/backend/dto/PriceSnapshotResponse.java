package com.hqa.backend.dto;

import java.time.OffsetDateTime;

public record PriceSnapshotResponse(
        String stockCode,
        Long currentPrice,
        OffsetDateTime snapshotAt,
        String source,
        boolean success,
        String failureReason
) {
}
