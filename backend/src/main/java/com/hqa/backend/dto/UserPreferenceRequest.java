package com.hqa.backend.dto;

import com.hqa.backend.entity.enums.InvestmentExperience;
import com.hqa.backend.entity.enums.InvestmentGoal;
import com.hqa.backend.entity.enums.InvestmentType;
import com.hqa.backend.entity.enums.LossAction;
import com.hqa.backend.entity.enums.LossTolerance;
import com.hqa.backend.entity.enums.OccupationType;
import com.hqa.backend.entity.enums.VolatilityTolerance;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Past;
import java.time.LocalDate;

public record UserPreferenceRequest(
        /** 현재 투자 가능한 총 재산 (원, 0 이상) */
        @NotNull @Min(0) Long totalAssets,

        /** 월별 투자 추가 가능 금액 (원, 0 이상) */
        @NotNull @Min(0) Long monthlyInvestment,

        /** 투자 기간 (개월, 1~600 → 최대 50년) */
        @NotNull @Min(1) @Max(600) Integer investmentPeriodMonths,

        /** 목표 수익률 (%, 1~1000) */
        @NotNull @Min(1) @Max(1000) Integer targetReturnRate,

        /** 투자 목적 */
        @NotNull InvestmentGoal investmentGoal,

        /** 투자 경험 */
        @NotNull InvestmentExperience investmentExperience,

        /** 생년월일 (과거 날짜여야 함) */
        @NotNull @Past LocalDate birthDate,

        /** 투자 유형 */
        @NotNull InvestmentType investmentType,

        /** 변동성 허용 수준 */
        @NotNull VolatilityTolerance volatilityTolerance,

        /** 손실 시 행동 */
        @NotNull LossAction lossAction,

        /** 레버리지 사용 허용 여부 */
        @NotNull Boolean leverageAllowed,

        /** 직업 유형 */
        @NotNull OccupationType occupationType,

        /** 손실 허용도 */
        @NotNull LossTolerance lossTolerance
) {
}
