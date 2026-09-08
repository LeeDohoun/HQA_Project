package com.hqa.backend.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import java.time.OffsetDateTime;

@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record PriceSnapshotResponse(
        String stockCode,
        Long currentPrice,
        OffsetDateTime snapshotAt,
        String source,
        boolean success,
        String failureReason
) {
}
