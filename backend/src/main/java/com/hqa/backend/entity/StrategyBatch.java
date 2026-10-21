package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.UUID;

/** One rebalancing batch for one strategy account, session and auction phase. */
@Entity
@Table(name = "strategy_batches")
public class StrategyBatch {
    @Id
    private UUID id;
    @Column(nullable = false)
    private String strategyId;
    @Column(nullable = false)
    private String userId;
    @Column(nullable = false)
    private String accountBinding;
    @Column(nullable = false)
    private String decisionHash;
    @Column(nullable = false)
    private LocalDate decisionDate;
    @Column(nullable = false)
    private LocalDate sessionDate;
    @Column(nullable = false)
    private String phase;
    @Column(nullable = false)
    private String status;
    @Column(nullable = false, unique = true)
    private String idempotencyKey;
    private Long expectedNavKrw;
    @Column(columnDefinition = "TEXT")
    private String reasons;
    private Double costReserveRate;
    private OffsetDateTime lastReconciledAt;
    @Column(nullable = false)
    private OffsetDateTime createdAt;
    private OffsetDateTime submittedAt;
    private OffsetDateTime completedAt;
    @Version
    @Column(nullable = false)
    private long rowVersion;

    public StrategyBatch() {
        this.id = UUID.randomUUID();
    }

    public UUID getId() { return id; }
    public String getStrategyId() { return strategyId; }
    public void setStrategyId(String strategyId) { this.strategyId = strategyId; }
    public String getUserId() { return userId; }
    public void setUserId(String userId) { this.userId = userId; }
    public String getAccountBinding() { return accountBinding; }
    public void setAccountBinding(String accountBinding) { this.accountBinding = accountBinding; }
    public String getDecisionHash() { return decisionHash; }
    public void setDecisionHash(String decisionHash) { this.decisionHash = decisionHash; }
    public LocalDate getDecisionDate() { return decisionDate; }
    public void setDecisionDate(LocalDate decisionDate) { this.decisionDate = decisionDate; }
    public LocalDate getSessionDate() { return sessionDate; }
    public void setSessionDate(LocalDate sessionDate) { this.sessionDate = sessionDate; }
    public String getPhase() { return phase; }
    public void setPhase(String phase) { this.phase = phase; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getIdempotencyKey() { return idempotencyKey; }
    public void setIdempotencyKey(String idempotencyKey) { this.idempotencyKey = idempotencyKey; }
    public Long getExpectedNavKrw() { return expectedNavKrw; }
    public void setExpectedNavKrw(Long expectedNavKrw) { this.expectedNavKrw = expectedNavKrw; }
    public String getReasons() { return reasons; }
    public Double getCostReserveRate() { return costReserveRate; }
    public OffsetDateTime getLastReconciledAt() { return lastReconciledAt; }
    public void setLastReconciledAt(OffsetDateTime lastReconciledAt) { this.lastReconciledAt = lastReconciledAt; }
    public void setCostReserveRate(Double costReserveRate) { this.costReserveRate = costReserveRate; }
    public void setReasons(String reasons) { this.reasons = reasons; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
    public OffsetDateTime getSubmittedAt() { return submittedAt; }
    public void setSubmittedAt(OffsetDateTime submittedAt) { this.submittedAt = submittedAt; }
    public OffsetDateTime getCompletedAt() { return completedAt; }
    public void setCompletedAt(OffsetDateTime completedAt) { this.completedAt = completedAt; }
    public long getRowVersion() { return rowVersion; }
}
