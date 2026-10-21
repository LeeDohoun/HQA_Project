package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;
import org.hibernate.annotations.Immutable;

/** Cash sizing of a CONT_BUY batch, written once before its first order and never updated. */
@Entity
@Immutable
@Table(name = "strategy_batch_sizing")
public class StrategyBatchSizing {
    @Id
    private UUID batchId;
    @Column(nullable = false)
    private long orderableCashKrw;
    @Column(nullable = false)
    private OffsetDateTime readAt;
    /** Raw KIS VTTC8908R output fields as JSON. */
    @Column(nullable = false, columnDefinition = "TEXT")
    private String kisFields;
    @Column(nullable = false)
    private long requiredKrw;
    @Column(nullable = false)
    private double costReserveRate;
    @Column(nullable = false)
    private double scaleFactor;
    @Column(nullable = false)
    private boolean scaled;
    /** JSON list of {seq, stockCode, refPrice, plannedQuantity, finalQuantity}. */
    @Column(nullable = false, columnDefinition = "TEXT")
    private String quantities;

    protected StrategyBatchSizing() { }

    public StrategyBatchSizing(UUID batchId, long orderableCashKrw, OffsetDateTime readAt, String kisFields, long requiredKrw,
            double costReserveRate, double scaleFactor, boolean scaled, String quantities) {
        this.batchId = batchId;
        this.orderableCashKrw = orderableCashKrw;
        this.readAt = readAt;
        this.kisFields = kisFields;
        this.requiredKrw = requiredKrw;
        this.costReserveRate = costReserveRate;
        this.scaleFactor = scaleFactor;
        this.scaled = scaled;
        this.quantities = quantities;
    }

    public UUID getBatchId() { return batchId; }
    public long getOrderableCashKrw() { return orderableCashKrw; }
    public OffsetDateTime getReadAt() { return readAt; }
    public String getKisFields() { return kisFields; }
    public long getRequiredKrw() { return requiredKrw; }
    public double getCostReserveRate() { return costReserveRate; }
    public double getScaleFactor() { return scaleFactor; }
    public boolean isScaled() { return scaled; }
    public String getQuantities() { return quantities; }
}
