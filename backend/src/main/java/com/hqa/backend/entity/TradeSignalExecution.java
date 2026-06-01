package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "trade_signal_executions")
public class TradeSignalExecution {

    @Id
    private String id;
    @Column(nullable = false)
    private String signalId;
    @Column(nullable = false)
    private String userId;
    @Column(nullable = false)
    private String status;
    private Integer quantity;
    private Long orderPrice;
    private Long currentPrice;
    private Double priceDriftPct;
    private String rejectReason;
    @Column(columnDefinition = "TEXT")
    private String kisResponse;
    private OffsetDateTime executedAt;

    @PrePersist
    public void onCreate() {
        if (id == null) id = UUID.randomUUID().toString();
        if (executedAt == null) executedAt = OffsetDateTime.now();
    }

    public String getId() { return id; }
    public String getSignalId() { return signalId; }
    public void setSignalId(String signalId) { this.signalId = signalId; }
    public String getUserId() { return userId; }
    public void setUserId(String userId) { this.userId = userId; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public Integer getQuantity() { return quantity; }
    public void setQuantity(Integer quantity) { this.quantity = quantity; }
    public Long getOrderPrice() { return orderPrice; }
    public void setOrderPrice(Long orderPrice) { this.orderPrice = orderPrice; }
    public Long getCurrentPrice() { return currentPrice; }
    public void setCurrentPrice(Long currentPrice) { this.currentPrice = currentPrice; }
    public Double getPriceDriftPct() { return priceDriftPct; }
    public void setPriceDriftPct(Double priceDriftPct) { this.priceDriftPct = priceDriftPct; }
    public String getRejectReason() { return rejectReason; }
    public void setRejectReason(String rejectReason) { this.rejectReason = rejectReason; }
    public String getKisResponse() { return kisResponse; }
    public void setKisResponse(String kisResponse) { this.kisResponse = kisResponse; }
    public OffsetDateTime getExecutedAt() { return executedAt; }
}
