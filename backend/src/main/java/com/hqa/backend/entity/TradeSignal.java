package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "trade_signals")
public class TradeSignal {

    @Id
    private String id;

    @Column(nullable = false)
    private String userId;
    @Column(nullable = false)
    private String source;
    private String strategyProfile = "default";
    private String themeKey;
    private String themeName;
    @Column(nullable = false)
    private String stockCode;
    @Column(nullable = false)
    private String stockName;
    @Column(nullable = false)
    private String action;
    private Integer leaderScore;
    private Integer confidence;
    private String riskLevel;
    private String positionSize;
    private Long signalPrice;
    private String stopLoss;
    @Column(columnDefinition = "TEXT")
    private String reason;
    @Column(nullable = false)
    private String status = "PENDING";
    private String rejectReason;
    @Column(columnDefinition = "TEXT")
    private String rawPayload;
    @Column(columnDefinition = "TEXT")
    private String tradePlanJson;
    @Column(columnDefinition = "TEXT")
    private String conditionPayload;
    @Column(unique = true)
    private String idempotencyKey;
    private OffsetDateTime expiresAt;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
    private OffsetDateTime executedAt;

    @PrePersist
    public void onCreate() {
        if (id == null) id = UUID.randomUUID().toString();
        OffsetDateTime now = OffsetDateTime.now();
        createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    public void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }

    public String getId() { return id; }
    public String getUserId() { return userId; }
    public void setUserId(String userId) { this.userId = userId; }
    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }
    public String getStrategyProfile() { return strategyProfile; }
    public void setStrategyProfile(String strategyProfile) { this.strategyProfile = strategyProfile; }
    public String getThemeKey() { return themeKey; }
    public void setThemeKey(String themeKey) { this.themeKey = themeKey; }
    public String getThemeName() { return themeName; }
    public void setThemeName(String themeName) { this.themeName = themeName; }
    public String getStockCode() { return stockCode; }
    public void setStockCode(String stockCode) { this.stockCode = stockCode; }
    public String getStockName() { return stockName; }
    public void setStockName(String stockName) { this.stockName = stockName; }
    public String getAction() { return action; }
    public void setAction(String action) { this.action = action; }
    public Integer getLeaderScore() { return leaderScore; }
    public void setLeaderScore(Integer leaderScore) { this.leaderScore = leaderScore; }
    public Integer getConfidence() { return confidence; }
    public void setConfidence(Integer confidence) { this.confidence = confidence; }
    public String getRiskLevel() { return riskLevel; }
    public void setRiskLevel(String riskLevel) { this.riskLevel = riskLevel; }
    public String getPositionSize() { return positionSize; }
    public void setPositionSize(String positionSize) { this.positionSize = positionSize; }
    public Long getSignalPrice() { return signalPrice; }
    public void setSignalPrice(Long signalPrice) { this.signalPrice = signalPrice; }
    public String getStopLoss() { return stopLoss; }
    public void setStopLoss(String stopLoss) { this.stopLoss = stopLoss; }
    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getRejectReason() { return rejectReason; }
    public void setRejectReason(String rejectReason) { this.rejectReason = rejectReason; }
    public String getRawPayload() { return rawPayload; }
    public void setRawPayload(String rawPayload) { this.rawPayload = rawPayload; }
    public String getTradePlanJson() { return tradePlanJson; }
    public void setTradePlanJson(String tradePlanJson) { this.tradePlanJson = tradePlanJson; }
    public String getConditionPayload() { return conditionPayload; }
    public void setConditionPayload(String conditionPayload) { this.conditionPayload = conditionPayload; }
    public String getIdempotencyKey() { return idempotencyKey; }
    public void setIdempotencyKey(String idempotencyKey) { this.idempotencyKey = idempotencyKey; }
    public OffsetDateTime getExpiresAt() { return expiresAt; }
    public void setExpiresAt(OffsetDateTime expiresAt) { this.expiresAt = expiresAt; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public OffsetDateTime getExecutedAt() { return executedAt; }
    public void setExecutedAt(OffsetDateTime executedAt) { this.executedAt = executedAt; }
}
