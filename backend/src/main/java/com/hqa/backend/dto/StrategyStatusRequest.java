package com.hqa.backend.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import java.time.OffsetDateTime;
import java.util.Map;

@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record StrategyStatusRequest(String strategyId, OffsetDateTime generatedAt, Map<String, Object> payload) { }
