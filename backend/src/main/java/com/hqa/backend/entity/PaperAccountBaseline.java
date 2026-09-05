package com.hqa.backend.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.LocalDate;
import java.time.OffsetDateTime;

@Entity
@Table(name = "paper_account_baselines")
public class PaperAccountBaseline {
    @Id private String id;
    private String userId;
    private LocalDate tradingDate;
    private Long baselineEquity;
    private OffsetDateTime capturedAt;
    private String source;

    protected PaperAccountBaseline() { }
    public PaperAccountBaseline(String userId, LocalDate date, long equity, OffsetDateTime at, String source) {
        this.id = userId + ":" + date;
        this.userId = userId;
        this.tradingDate = date;
        this.baselineEquity = equity;
        this.capturedAt = at;
        this.source = source;
    }
    public Long getBaselineEquity() { return baselineEquity; }
    public String getSource() { return source; }
}
