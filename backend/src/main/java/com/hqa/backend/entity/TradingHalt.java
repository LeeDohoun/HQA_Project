package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;

/** Halt switch for strategy orders: scope GLOBAL or a userId. */
@Entity
@Table(name = "trading_halts")
public class TradingHalt {
    public static final String GLOBAL = "GLOBAL";
    @Id
    private String scope;
    @Column(nullable = false)
    private boolean halted;
    @Column(columnDefinition = "TEXT")
    private String reason;
    @Column(nullable = false)
    private OffsetDateTime updatedAt;

    protected TradingHalt() { }

    public TradingHalt(String scope) {
        this.scope = scope;
    }

    public String getScope() { return scope; }
    public boolean isHalted() { return halted; }
    public void setHalted(boolean halted) { this.halted = halted; }
    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
}
