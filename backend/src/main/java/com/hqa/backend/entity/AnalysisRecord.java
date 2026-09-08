package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "analysis_records", indexes = {
        @Index(name = "ix_analysis_task_id", columnList = "taskId", unique = true),
        @Index(name = "ix_analysis_stock_date", columnList = "stockCode, createdAt")
})
public class AnalysisRecord {

    @Id
    private String id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id")
    private User user;

    @Column(nullable = false, unique = true)
    private String taskId;
    private String stockName;
    private String stockCode;
    private String mode = "full";
    private int maxRetries = 1;
    private String status = "pending";

    @Column(columnDefinition = "TEXT")
    private String analystResult;
    @Column(columnDefinition = "TEXT")
    private String quantResult;
    @Column(columnDefinition = "TEXT")
    private String chartistResult;
    @Column(columnDefinition = "TEXT")
    private String finalDecision;

    private String researchQuality;
    @Column(columnDefinition = "TEXT")
    private String qualityWarnings;
    private Double totalScore;
    private String action;
    private Double confidence;
    @Column(columnDefinition = "TEXT")
    private String errors;
    private OffsetDateTime createdAt = OffsetDateTime.now();
    private OffsetDateTime completedAt;
    private Double durationSeconds;

    @Column(columnDefinition = "TEXT")
    private String resultJson;

    @PrePersist
    public void onCreate() {
        if (id == null) {
            id = UUID.randomUUID().toString();
        }
        if (createdAt == null) {
            createdAt = OffsetDateTime.now();
        }
    }

    public String getId() { return id; }
    public User getUser() { return user; }
    public void setUser(User value) { user = value; }
    public String getTaskId() { return taskId; }
    public void setTaskId(String value) { taskId = value; }
    public String getStockName() { return stockName; }
    public void setStockName(String value) { stockName = value; }
    public String getStockCode() { return stockCode; }
    public void setStockCode(String value) { stockCode = value; }
    public String getMode() { return mode; }
    public void setMode(String value) { mode = value; }
    public int getMaxRetries() { return maxRetries; }
    public void setMaxRetries(int value) { maxRetries = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getCompletedAt() { return completedAt; }
    public void setCompletedAt(OffsetDateTime value) { completedAt = value; }
    public String getResultJson() { return resultJson; }
    public void setResultJson(String value) { resultJson = value; }
}
