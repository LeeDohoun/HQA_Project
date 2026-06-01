package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.time.OffsetDateTime;
import java.util.Map;

public record InternalTradeSignalRequest(
        @NotBlank String userId,
        @NotBlank String source,
        String strategyProfile,
        String themeKey,
        String themeName,
        @NotBlank String stockCode,
        @NotBlank String stockName,
        @NotBlank String action,
        Integer leaderScore,
        Integer confidence,
        String riskLevel,
        String positionSize,
        Long signalPrice,
        String stopLoss,
        String reason,
        @NotNull OffsetDateTime expiresAt,
        Map<String, Object> rawPayload
) {
}
