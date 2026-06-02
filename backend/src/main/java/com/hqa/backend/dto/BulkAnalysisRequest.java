package com.hqa.backend.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import java.util.List;

public class BulkAnalysisRequest {

    private List<BulkAnalysisItem> items = List.of();

    public List<BulkAnalysisItem> getItems() {
        return items;
    }

    public void setItems(List<BulkAnalysisItem> items) {
        this.items = items == null ? List.of() : items;
    }

    public static class BulkAnalysisItem {
        private String stockName;
        private String stockCode;

        public String getStockName() {
            return stockName;
        }

        @JsonAlias({"stockName", "stock_name"})
        public void setStockName(String stockName) {
            this.stockName = stockName;
        }

        public String getStockCode() {
            return stockCode;
        }

        @JsonAlias({"stockCode", "stock_code"})
        public void setStockCode(String stockCode) {
            this.stockCode = stockCode;
        }
    }
}
