package com.hqa.backend.dto;

import com.hqa.backend.entity.enums.UserRole;
import java.time.OffsetDateTime;

public record AuthUserResponse(
        String id,
        String userId,
        String firstName,
        String lastName,
        UserRole role,
        boolean active,
        boolean kisConfigured,
        boolean surveyCompleted,
        boolean autoTradeEnabled,
        OffsetDateTime createdAt
) {
}
