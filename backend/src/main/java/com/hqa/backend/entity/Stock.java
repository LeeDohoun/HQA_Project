package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;

@Entity
@Table(name = "stocks")
public class Stock {

    @Id
    @Column(length = 12)
    private String code;

    @Column(name = "name_ko", nullable = false, columnDefinition = "TEXT")
    private String nameKo;

    @Column(name = "name_en", columnDefinition = "TEXT")
    private String nameEn;

    @Column(nullable = false, length = 16)
    private String market;

    @Column(name = "is_tradable", nullable = false)
    private boolean tradable = true;

    @Column(name = "auto_trade_eligible", nullable = false)
    private boolean autoTradeEligible = false;

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    public Stock() {
    }

    public Stock(String code, String nameKo, String nameEn, String market) {
        this.code = code;
        this.nameKo = nameKo;
        this.nameEn = nameEn;
        this.market = market;
    }

    @PrePersist
    @PreUpdate
    public void touch() {
        updatedAt = OffsetDateTime.now();
    }

    public String getCode() {
        return code;
    }

    public void setCode(String code) {
        this.code = code;
    }

    public String getNameKo() {
        return nameKo;
    }

    public void setNameKo(String nameKo) {
        this.nameKo = nameKo;
    }

    public String getNameEn() {
        return nameEn;
    }

    public void setNameEn(String nameEn) {
        this.nameEn = nameEn;
    }

    public String getMarket() {
        return market;
    }

    public void setMarket(String market) {
        this.market = market;
    }

    public boolean isTradable() {
        return tradable;
    }

    public void setTradable(boolean tradable) {
        this.tradable = tradable;
    }

    public boolean isAutoTradeEligible() {
        return autoTradeEligible;
    }

    public void setAutoTradeEligible(boolean autoTradeEligible) {
        this.autoTradeEligible = autoTradeEligible;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }
}
