package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "trade_plan_receipts")
public class TradePlanReceipt {
    @Id @Column(length = 512) private String id;
    private String userId;
    private String signalId;
    protected TradePlanReceipt() { }
    public TradePlanReceipt(String id, String userId, String signalId) {
        this.id = id;
        this.userId = userId;
        this.signalId = signalId;
    }
    public String getUserId() { return userId; }
    public String getSignalId() { return signalId; }
}
