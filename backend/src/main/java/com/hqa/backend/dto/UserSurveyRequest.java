package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record UserSurveyRequest(
        @NotBlank @Size(max = 100) String investmentExperience,
        @NotBlank @Size(max = 100) String riskTolerance,
        @NotBlank @Size(max = 100) String investmentGoal,
        @NotBlank @Size(max = 100) String preferredMarket,
        @Size(max = 5000) String notes
) {
}
