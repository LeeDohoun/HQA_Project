package com.hqa.backend.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import java.time.LocalDate;
import java.util.List;

/**
 * Internal batch request from the Python strategy runtime (docs/strategy-runtime.md). costReserveRate
 * applies to CONT_BUY only (0 <= r < 0.05; default 0.003). exemption (optional): BB_SCHEDULED_EXIT or
 * TERMINATION_LIQUIDATION, which lifts only the cancel-ratio limit for a SELL-only batch.
 */
@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record StrategyBatchRequest(String strategyId, String userId, String decisionHash, LocalDate decisionDate,
        LocalDate sessionDate, String phase, String idempotencyKey, Boolean dryRun, Long expectedNavKrw,
        List<Order> orders, Double costReserveRate, String exemption) {

    public StrategyBatchRequest(String strategyId, String userId, String decisionHash, LocalDate decisionDate,
            LocalDate sessionDate, String phase, String idempotencyKey, Boolean dryRun, Long expectedNavKrw,
            List<Order> orders, Double costReserveRate) {
        this(strategyId, userId, decisionHash, decisionDate, sessionDate, phase, idempotencyKey, dryRun, expectedNavKrw,
                orders, costReserveRate, null);
    }

    public StrategyBatchRequest(String strategyId, String userId, String decisionHash, LocalDate decisionDate,
            LocalDate sessionDate, String phase, String idempotencyKey, Boolean dryRun, Long expectedNavKrw,
            List<Order> orders) {
        this(strategyId, userId, decisionHash, decisionDate, sessionDate, phase, idempotencyKey, dryRun, expectedNavKrw,
                orders, null, null);
    }

    @JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
    public record Order(Integer seq, String stockCode, String side, Long quantity, Long refPrice, Double adv20Krw) { }
}
