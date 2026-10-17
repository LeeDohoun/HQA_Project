package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;

/** Highest observed equity of a PAPER broker account, for the drawdown stop. */
@Entity
@Table(name = "account_equity_peaks")
public class AccountEquityPeak {
    @Id
    private String accountBinding;
    @Column(nullable = false)
    private long peakEquityKrw;
    @Column(nullable = false)
    private OffsetDateTime updatedAt;

    protected AccountEquityPeak() { }

    public AccountEquityPeak(String accountBinding, long peakEquityKrw, OffsetDateTime updatedAt) {
        this.accountBinding = accountBinding;
        this.peakEquityKrw = peakEquityKrw;
        this.updatedAt = updatedAt;
    }

    public String getAccountBinding() { return accountBinding; }
    public long getPeakEquityKrw() { return peakEquityKrw; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
}
