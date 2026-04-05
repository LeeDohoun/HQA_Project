package com.hqa.backend.dto;

public record RecommendRequest(
        String investmentExperience,
        String riskTolerance,
        String investmentGoal,
        String preferredMarket,
        String notes
) {}
