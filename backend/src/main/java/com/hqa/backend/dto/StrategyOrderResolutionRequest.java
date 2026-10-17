package com.hqa.backend.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

/** Operator finding for an UNKNOWN strategy order: action "adopt" (with brokerOrderId) or "not-submitted". */
@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record StrategyOrderResolutionRequest(String action, String brokerOrderId, String note) { }
