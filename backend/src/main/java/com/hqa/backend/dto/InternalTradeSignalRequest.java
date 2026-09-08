package com.hqa.backend.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import jakarta.validation.constraints.NotBlank;
import java.time.OffsetDateTime;
import java.util.Map;

@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
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
        OffsetDateTime expiresAt,
        Map<String, Object> rawPayload,
        Map<String, Object> tradePlanJson,
        Map<String, Object> conditionPayload,
        String idempotencyKey,
        Integer planVersion,
        OffsetDateTime entryValidUntil,
        OffsetDateTime plannedExitAt,
        Double targetPositionPct,
        String accountMode,
        String analysisId,
        OffsetDateTime analysisAsOf
) {
    public InternalTradeSignalRequest(String userId, String source, String strategyProfile,
            String themeKey, String themeName, String stockCode, String stockName, String action,
            Integer leaderScore, Integer confidence, String riskLevel, String positionSize,
            Long signalPrice, String stopLoss, String reason, OffsetDateTime expiresAt,
            Map<String, Object> rawPayload, Map<String, Object> tradePlanJson,
            Map<String, Object> conditionPayload, String idempotencyKey) {
        this(userId, source, strategyProfile, themeKey, themeName, stockCode, stockName, action,
                leaderScore, confidence, riskLevel, positionSize, signalPrice, stopLoss, reason,
                expiresAt, rawPayload, tradePlanJson, conditionPayload, idempotencyKey, 1, null, null, null, "PAPER", null, null);
    }
}
