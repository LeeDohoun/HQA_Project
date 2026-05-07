package com.hqa.backend.entity;

import com.hqa.backend.entity.enums.InvestmentExperience;
import com.hqa.backend.entity.enums.InvestmentGoal;
import com.hqa.backend.entity.enums.InvestmentType;
import com.hqa.backend.entity.enums.LossAction;
import com.hqa.backend.entity.enums.LossTolerance;
import com.hqa.backend.entity.enums.OccupationType;
import com.hqa.backend.entity.enums.VolatilityTolerance;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.OneToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "user_preferences")
public class UserPreference {

    @Id
    private String id;

    /** 현재 투자 가능한 총 재산 (원, 0 이상) */
    @Column(nullable = false)
    private Long totalAssets;

    /** 월별 투자 추가 가능 금액 (원, 0 이상) */
    @Column(nullable = false)
    private Long monthlyInvestment;

    /** 투자 기간 (개월, 1~600) */
    @Column(nullable = false)
    private Integer investmentPeriodMonths;

    /** 목표 수익률 (%, 1~1000) */
    @Column(nullable = false)
    private Integer targetReturnRate;

    /** 투자 목적 */
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private InvestmentGoal investmentGoal;

    /** 투자 경험 */
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private InvestmentExperience investmentExperience;

    /** 나이 — 생년월일 */
    @Column(nullable = false)
    private LocalDate birthDate;

    /** 투자 유형 */
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private InvestmentType investmentType;

    /** 변동성 허용 수준 */
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private VolatilityTolerance volatilityTolerance;

    /** 손실 시 행동 */
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private LossAction lossAction;

    /** 레버리지 사용 허용 여부 */
    @Column(nullable = false)
    private Boolean leverageAllowed;

    /** 직업 유형 */
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private OccupationType occupationType;

    /** 손실 허용도 */
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private LossTolerance lossTolerance;

    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false, unique = true)
    private User user;

    @PrePersist
    public void onCreate() {
        if (id == null) {
            id = UUID.randomUUID().toString();
        }
        OffsetDateTime now = OffsetDateTime.now();
        createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    public void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }

    public String getId() { return id; }

    public Long getTotalAssets() { return totalAssets; }
    public void setTotalAssets(Long totalAssets) { this.totalAssets = totalAssets; }

    public Long getMonthlyInvestment() { return monthlyInvestment; }
    public void setMonthlyInvestment(Long monthlyInvestment) { this.monthlyInvestment = monthlyInvestment; }

    public Integer getInvestmentPeriodMonths() { return investmentPeriodMonths; }
    public void setInvestmentPeriodMonths(Integer investmentPeriodMonths) { this.investmentPeriodMonths = investmentPeriodMonths; }

    public Integer getTargetReturnRate() { return targetReturnRate; }
    public void setTargetReturnRate(Integer targetReturnRate) { this.targetReturnRate = targetReturnRate; }

    public InvestmentGoal getInvestmentGoal() { return investmentGoal; }
    public void setInvestmentGoal(InvestmentGoal investmentGoal) { this.investmentGoal = investmentGoal; }

    public InvestmentExperience getInvestmentExperience() { return investmentExperience; }
    public void setInvestmentExperience(InvestmentExperience investmentExperience) { this.investmentExperience = investmentExperience; }

    public LocalDate getBirthDate() { return birthDate; }
    public void setBirthDate(LocalDate birthDate) { this.birthDate = birthDate; }

    public InvestmentType getInvestmentType() { return investmentType; }
    public void setInvestmentType(InvestmentType investmentType) { this.investmentType = investmentType; }

    public VolatilityTolerance getVolatilityTolerance() { return volatilityTolerance; }
    public void setVolatilityTolerance(VolatilityTolerance volatilityTolerance) { this.volatilityTolerance = volatilityTolerance; }

    public LossAction getLossAction() { return lossAction; }
    public void setLossAction(LossAction lossAction) { this.lossAction = lossAction; }

    public Boolean getLeverageAllowed() { return leverageAllowed; }
    public void setLeverageAllowed(Boolean leverageAllowed) { this.leverageAllowed = leverageAllowed; }

    public OccupationType getOccupationType() { return occupationType; }
    public void setOccupationType(OccupationType occupationType) { this.occupationType = occupationType; }

    public LossTolerance getLossTolerance() { return lossTolerance; }
    public void setLossTolerance(LossTolerance lossTolerance) { this.lossTolerance = lossTolerance; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }

    public User getUser() { return user; }
    public void setUser(User user) { this.user = user; }
}
