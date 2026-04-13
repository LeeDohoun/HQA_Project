package com.hqa.backend.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Lob;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "stock_cache", indexes = {
        @Index(name = "ix_stock_cache_type", columnList = "stockCode, dataType")
})
public class StockCache {

    @Id
    private String id;
    private String stockCode;
    private String stockName;
    private String dataType;
    @Lob
    private String data;
    private OffsetDateTime expiresAt;
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @PrePersist
    public void onCreate() {
        if (id == null) {
            id = UUID.randomUUID().toString();
        }
        if (createdAt == null) {
            createdAt = OffsetDateTime.now();
        }
    }
}
