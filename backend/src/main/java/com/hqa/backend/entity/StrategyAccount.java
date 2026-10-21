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
    /** First time the account was enabled at stage paper, with the PAPER equity then (termination base). */
    private OffsetDateTime activatedAt;
    private Long activationNavKrw;
    /** Set once by the close-to-close termination check; never cleared (no resume under the same ID). */
    private OffsetDateTime terminatedAt;
    private String terminationReason;

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
    public OffsetDateTime getActivatedAt() { return activatedAt; }
    public Long getActivationNavKrw() { return activationNavKrw; }
    public void activate(OffsetDateTime at, Long navKrw) {
        if (activatedAt != null) throw new IllegalStateException("STRATEGY_ACCOUNT_ALREADY_ACTIVATED");
        this.activatedAt = at;
        this.activationNavKrw = navKrw;
    }
    public OffsetDateTime getTerminatedAt() { return terminatedAt; }
    public String getTerminationReason() { return terminationReason; }
    public boolean isTerminated() { return terminatedAt != null; }
    public void terminate(OffsetDateTime at, String reason) {
        if (terminatedAt != null) return;
        this.terminatedAt = at;
        this.terminationReason = reason;
    }
}
