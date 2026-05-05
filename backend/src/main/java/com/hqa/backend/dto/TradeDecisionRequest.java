package com.hqa.backend.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

public class TradeDecisionRequest {

    @NotBlank
    private String stockName;

    @NotBlank
    @Pattern(regexp = "^\\d{6}$")
    private String stockCode;

    @Valid
    @NotNull
    private TradeDecisionPayload finalDecision;

    private Integer currentPrice;
    private int quantity = 0;
    private Boolean dryRunOverride;
    private Boolean tradingEnabledOverride;

    public String getStockName() { return stockName; }
    public void setStockName(String stockName) { this.stockName = stockName; }
    public String getStockCode() { return stockCode; }
    public void setStockCode(String stockCode) { this.stockCode = stockCode; }
    public TradeDecisionPayload getFinalDecision() { return finalDecision; }
    public void setFinalDecision(TradeDecisionPayload finalDecision) { this.finalDecision = finalDecision; }
    public Integer getCurrentPrice() { return currentPrice; }
    public void setCurrentPrice(Integer currentPrice) { this.currentPrice = currentPrice; }
    public int getQuantity() { return quantity; }
    public void setQuantity(int quantity) { this.quantity = quantity; }
    public Boolean getDryRunOverride() { return dryRunOverride; }
    public void setDryRunOverride(Boolean dryRunOverride) { this.dryRunOverride = dryRunOverride; }
    public Boolean getTradingEnabledOverride() { return tradingEnabledOverride; }
    public void setTradingEnabledOverride(Boolean tradingEnabledOverride) { this.tradingEnabledOverride = tradingEnabledOverride; }
}
