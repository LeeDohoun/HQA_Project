package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
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
    private String orderId;
    private String orderType;
    private Integer quantity;
    private Integer submittedQuantity;
    private Integer filledQuantity;
    private Long orderPrice;
    private Long averageFillPrice;
    private Long currentPrice;
    private Double priceDriftPct;
    private String rejectReason;
    @Column(columnDefinition = "TEXT")
    private String kisResponse;
    private OffsetDateTime submittedAt;
    private OffsetDateTime filledAt;
    private OffsetDateTime orderExpiresAt;
    private OffsetDateTime executedAt;
    @Column(unique = true)
    private String triggerKey;
    private String triggerType;
    private String orderSide;
    private String stockCode;
    private String orderOrganization;
    private Long reservedCash = 0L;
    private String accountBinding;
    @Version
    private Long rowVersion;

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
    public String getOrderId() { return orderId; }
    public void setOrderId(String orderId) { this.orderId = orderId; }
    public String getOrderType() { return orderType; }
    public void setOrderType(String orderType) { this.orderType = orderType; }
    public Integer getQuantity() { return quantity; }
    public void setQuantity(Integer quantity) { this.quantity = quantity; }
    public Integer getSubmittedQuantity() { return submittedQuantity; }
    public void setSubmittedQuantity(Integer submittedQuantity) { this.submittedQuantity = submittedQuantity; }
    public Integer getFilledQuantity() { return filledQuantity; }
    public void setFilledQuantity(Integer filledQuantity) { this.filledQuantity = filledQuantity; }
    public Long getOrderPrice() { return orderPrice; }
    public void setOrderPrice(Long orderPrice) { this.orderPrice = orderPrice; }
    public Long getAverageFillPrice() { return averageFillPrice; }
    public void setAverageFillPrice(Long averageFillPrice) { this.averageFillPrice = averageFillPrice; }
    public Long getCurrentPrice() { return currentPrice; }
    public void setCurrentPrice(Long currentPrice) { this.currentPrice = currentPrice; }
    public Double getPriceDriftPct() { return priceDriftPct; }
    public void setPriceDriftPct(Double priceDriftPct) { this.priceDriftPct = priceDriftPct; }
    public String getRejectReason() { return rejectReason; }
    public void setRejectReason(String rejectReason) { this.rejectReason = rejectReason; }
    public String getKisResponse() { return kisResponse; }
    public void setKisResponse(String kisResponse) { this.kisResponse = kisResponse; }
    public OffsetDateTime getSubmittedAt() { return submittedAt; }
    public void setSubmittedAt(OffsetDateTime submittedAt) { this.submittedAt = submittedAt; }
    public OffsetDateTime getFilledAt() { return filledAt; }
    public void setFilledAt(OffsetDateTime filledAt) { this.filledAt = filledAt; }
    public OffsetDateTime getOrderExpiresAt() { return orderExpiresAt; }
    public void setOrderExpiresAt(OffsetDateTime orderExpiresAt) { this.orderExpiresAt = orderExpiresAt; }
    public OffsetDateTime getExecutedAt() { return executedAt; }
    public String getTriggerKey() { return triggerKey; }
    public void setTriggerKey(String value) { triggerKey = value; }
    public String getTriggerType() { return triggerType; }
    public void setTriggerType(String value) { triggerType = value; }
    public String getOrderSide() { return orderSide; }
    public void setOrderSide(String value) { orderSide = value; }
    public String getStockCode() { return stockCode; }
    public void setStockCode(String value) { stockCode = value; }
    public String getOrderOrganization() { return orderOrganization; }
    public void setOrderOrganization(String value) { orderOrganization = value; }
    public Long getReservedCash() { return reservedCash; }
    public void setReservedCash(Long value) { reservedCash = value; }
    public String getAccountBinding() { return accountBinding; }
    public void setAccountBinding(String value) { accountBinding = value; }
}
