package com.hqa.backend.dto;

import java.time.OffsetDateTime;

public record HealthResponse(
        String status,
        String version,
        String environment,
        boolean langgraphAvailable,
        OffsetDateTime timestamp
) {
}
