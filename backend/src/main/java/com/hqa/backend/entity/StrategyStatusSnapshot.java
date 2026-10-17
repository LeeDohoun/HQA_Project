package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;

/** Latest status published by the Python strategy runtime, one row per strategy. */
@Entity
@Table(name = "strategy_status_snapshots")
public class StrategyStatusSnapshot {
    @Id
    private String strategyId;
    @Column(nullable = false, columnDefinition = "TEXT")
    private String payload;
    @Column(nullable = false)
    private OffsetDateTime generatedAt;
    @Column(nullable = false)
    private OffsetDateTime updatedAt;

    protected StrategyStatusSnapshot() { }

    public StrategyStatusSnapshot(String strategyId) {
        this.strategyId = strategyId;
    }

    public String getStrategyId() { return strategyId; }
    public String getPayload() { return payload; }
    public void setPayload(String payload) { this.payload = payload; }
    public OffsetDateTime getGeneratedAt() { return generatedAt; }
    public void setGeneratedAt(OffsetDateTime generatedAt) { this.generatedAt = generatedAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
}
