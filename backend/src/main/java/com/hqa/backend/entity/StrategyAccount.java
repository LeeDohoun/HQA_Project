package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;

/** A PAPER account dedicated to one strategy; it receives no LLM plans. */
@Entity
@Table(name = "strategy_accounts")
public class StrategyAccount {
    @Id
    private String userId;
    @Column(nullable = false, unique = true)
    private String strategyId;
    @Column(nullable = false)
    private String stage;
    @Column(nullable = false)
    private boolean enabled;
    @Column(nullable = false)
    private OffsetDateTime createdAt;
    @Column(nullable = false)
    private OffsetDateTime updatedAt;

    protected StrategyAccount() { }

    public StrategyAccount(String userId, String strategyId, String stage, boolean enabled, OffsetDateTime now) {
        this.userId = userId;
        this.strategyId = strategyId;
        this.stage = stage;
        this.enabled = enabled;
        this.createdAt = now;
        this.updatedAt = now;
    }

    public String getUserId() { return userId; }
    public String getStrategyId() { return strategyId; }
    public String getStage() { return stage; }
    public void setStage(String stage) { this.stage = stage; }
    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
}
