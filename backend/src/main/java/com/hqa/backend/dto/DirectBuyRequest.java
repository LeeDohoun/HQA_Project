package com.hqa.backend.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public class DirectBuyRequest {

    @NotBlank
    private String stockName;

    @NotBlank
    @Pattern(regexp = "^\\d{6}$")
    private String stockCode;

    @Min(1)
    private int quantity = 1;

    /** 0이면 시장가 주문 */
    @Min(0)
    private long limitPrice = 0;

    public String getStockName() { return stockName; }
    public void setStockName(String stockName) { this.stockName = stockName; }
    public String getStockCode() { return stockCode; }
    public void setStockCode(String stockCode) { this.stockCode = stockCode; }
    public int getQuantity() { return quantity; }
    public void setQuantity(int quantity) { this.quantity = quantity; }
    public long getLimitPrice() { return limitPrice; }
    public void setLimitPrice(long limitPrice) { this.limitPrice = limitPrice; }
}
