package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.UUID;

/** One order of a strategy batch. Broker acceptance (SUBMITTED) is not a fill. */
@Entity
@Table(name = "strategy_batch_orders")
public class StrategyBatchOrder {
    @Id
    private UUID id;
    @Column(nullable = false)
    private UUID batchId;
    @Column(nullable = false)
    private int seq;
    @Column(nullable = false)
    private String stockCode;
    @Column(nullable = false)
    private String side;
    @Column(nullable = false)
    private long quantity;
    @Column(nullable = false)
    private long refPrice;
    private Long plannedQuantity;
    private Long refPriceAtSubmit;
    @Column(name = "adv20_krw")
    private Double adv20Krw;
    @Column(nullable = false)
    private String orderType;
    @Column(nullable = false)
    private String status;
    private String brokerOrderId;
    private String orderOrg;
    private LocalDate orderDate;
    @Column(nullable = false)
    private long filledQuantity;
    @Column(precision = 20, scale = 4)
    private BigDecimal averageFillPrice;
    private String rejectReason;
    @Column(columnDefinition = "TEXT")
    private String kisResponse;
    private OffsetDateTime submittedAt;
    private OffsetDateTime cancelRequestedAt;
    @Column(nullable = false)
    private OffsetDateTime updatedAt;

    public StrategyBatchOrder() {
        this.id = UUID.randomUUID();
    }

    public boolean isBuy() { return "BUY".equals(side); }
    public long remainingQuantity() { return Math.max(0, quantity - filledQuantity); }

    public UUID getId() { return id; }
    public UUID getBatchId() { return batchId; }
    public void setBatchId(UUID batchId) { this.batchId = batchId; }
    public int getSeq() { return seq; }
    public void setSeq(int seq) { this.seq = seq; }
    public String getStockCode() { return stockCode; }
    public void setStockCode(String stockCode) { this.stockCode = stockCode; }
    public String getSide() { return side; }
    public void setSide(String side) { this.side = side; }
    public long getQuantity() { return quantity; }
    public void setQuantity(long quantity) { this.quantity = quantity; }
    public long getRefPrice() { return refPrice; }
    public Long getPlannedQuantity() { return plannedQuantity; }
    public void setPlannedQuantity(Long plannedQuantity) { this.plannedQuantity = plannedQuantity; }
    public Long getRefPriceAtSubmit() { return refPriceAtSubmit; }
    public void setRefPriceAtSubmit(Long refPriceAtSubmit) { this.refPriceAtSubmit = refPriceAtSubmit; }
    public void setRefPrice(long refPrice) { this.refPrice = refPrice; }
    public Double getAdv20Krw() { return adv20Krw; }
    public void setAdv20Krw(Double adv20Krw) { this.adv20Krw = adv20Krw; }
    public String getOrderType() { return orderType; }
    public void setOrderType(String orderType) { this.orderType = orderType; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getBrokerOrderId() { return brokerOrderId; }
    public void setBrokerOrderId(String brokerOrderId) { this.brokerOrderId = brokerOrderId; }
    public String getOrderOrg() { return orderOrg; }
    public void setOrderOrg(String orderOrg) { this.orderOrg = orderOrg; }
    public LocalDate getOrderDate() { return orderDate; }
    public void setOrderDate(LocalDate orderDate) { this.orderDate = orderDate; }
    public long getFilledQuantity() { return filledQuantity; }
    public void setFilledQuantity(long filledQuantity) { this.filledQuantity = filledQuantity; }
    public BigDecimal getAverageFillPrice() { return averageFillPrice; }
    public void setAverageFillPrice(BigDecimal averageFillPrice) { this.averageFillPrice = averageFillPrice; }
    public String getRejectReason() { return rejectReason; }
    public void setRejectReason(String rejectReason) { this.rejectReason = rejectReason; }
    public String getKisResponse() { return kisResponse; }
    public void setKisResponse(String kisResponse) { this.kisResponse = kisResponse; }
    public OffsetDateTime getSubmittedAt() { return submittedAt; }
    public void setSubmittedAt(OffsetDateTime submittedAt) { this.submittedAt = submittedAt; }
    public OffsetDateTime getCancelRequestedAt() { return cancelRequestedAt; }
    public void setCancelRequestedAt(OffsetDateTime cancelRequestedAt) { this.cancelRequestedAt = cancelRequestedAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
}
