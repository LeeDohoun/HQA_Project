package com.hqa.backend.dto;

import java.util.Map;

public record AutoTradeStatusResponse(boolean enabled, Map<String, Object> aiStatus) {}
