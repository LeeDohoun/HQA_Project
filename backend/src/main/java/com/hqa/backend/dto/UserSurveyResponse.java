package com.hqa.backend.dto;

import java.time.OffsetDateTime;

public record UserSurveyResponse(
        String investmentExperience,
        String riskTolerance,
        String investmentGoal,
        String preferredMarket,
        String notes,
        OffsetDateTime updatedAt
) {
}
