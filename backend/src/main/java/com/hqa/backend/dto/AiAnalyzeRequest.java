package com.hqa.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Payload sent from backend → AI server (POST /analyze).
 * Field names are pinned with @JsonProperty so they do not depend on
 * the global Jackson naming strategy.
 */
public record AiAnalyzeRequest(
        @JsonProperty("task_id") String taskId,
        @JsonProperty("stock_name") String stockName,
        @JsonProperty("stock_code") String stockCode,
        @JsonProperty("mode") String mode,
        @JsonProperty("max_retries") int maxRetries
) {}
