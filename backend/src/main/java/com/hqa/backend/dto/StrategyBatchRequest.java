package com.hqa.backend.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import java.time.LocalDate;
import java.util.List;

/**
 * Internal batch request from the Python strategy runtime (docs/strategy-runtime.md). costReserveRate
 * applies to CONT_BUY only (0 <= r < 0.05; default 0.003).
 */
@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record StrategyBatchRequest(String strategyId, String userId, String decisionHash, LocalDate decisionDate,
        LocalDate sessionDate, String phase, String idempotencyKey, Boolean dryRun, Long expectedNavKrw,
        List<Order> orders, Double costReserveRate) {

    public StrategyBatchRequest(String strategyId, String userId, String decisionHash, LocalDate decisionDate,
            LocalDate sessionDate, String phase, String idempotencyKey, Boolean dryRun, Long expectedNavKrw,
            List<Order> orders) {
        this(strategyId, userId, decisionHash, decisionDate, sessionDate, phase, idempotencyKey, dryRun, expectedNavKrw,
                orders, null);
    }

    @JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
    public record Order(Integer seq, String stockCode, String side, Long quantity, Long refPrice, Double adv20Krw) { }
}
