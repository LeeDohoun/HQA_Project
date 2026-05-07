package com.hqa.backend.dto;

import com.hqa.backend.entity.enums.InvestmentExperience;
import com.hqa.backend.entity.enums.InvestmentGoal;
import com.hqa.backend.entity.enums.InvestmentType;
import com.hqa.backend.entity.enums.LossAction;
import com.hqa.backend.entity.enums.LossTolerance;
import com.hqa.backend.entity.enums.OccupationType;
import com.hqa.backend.entity.enums.VolatilityTolerance;
import java.time.LocalDate;
import java.time.OffsetDateTime;

public record UserPreferenceResponse(
        Long totalAssets,
        Long monthlyInvestment,
        Integer investmentPeriodMonths,
        Integer targetReturnRate,
        InvestmentGoal investmentGoal,
        InvestmentExperience investmentExperience,
        LocalDate birthDate,
        InvestmentType investmentType,
        VolatilityTolerance volatilityTolerance,
        LossAction lossAction,
        Boolean leverageAllowed,
        OccupationType occupationType,
        LossTolerance lossTolerance,
        OffsetDateTime updatedAt
) {
}
