package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Lob;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "error_logs", indexes = {
        @Index(name = "ix_el_user_id", columnList = "userId"),
        @Index(name = "ix_el_created_at", columnList = "createdAt")
})
public class ErrorLog {

    @Id
    private String id = UUID.randomUUID().toString();

    private String userId;
    private String stockCode;

    @Column(nullable = false)
    private String source;

    @Column(nullable = false)
    private String message;

    @Lob
    private String detail;

    private OffsetDateTime createdAt = OffsetDateTime.now();

    @PrePersist
    public void onCreate() {
        createdAt = OffsetDateTime.now();
    }

    public String getId() {
        return id;
    }

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public String getStockCode() {
        return stockCode;
    }

    public void setStockCode(String stockCode) {
        this.stockCode = stockCode;
    }

    public String getSource() {
        return source;
    }

    public void setSource(String source) {
        this.source = source;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public String getDetail() {
        return detail;
    }

    public void setDetail(String detail) {
        this.detail = detail;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }
}
