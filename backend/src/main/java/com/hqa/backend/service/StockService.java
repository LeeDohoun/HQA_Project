package com.hqa.backend.service;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.RealtimePriceResponse;
import com.hqa.backend.dto.StockInfo;
import com.hqa.backend.dto.StockSearchResponse;
import com.hqa.backend.exception.ApiException;
import java.time.OffsetDateTime;
import org.springframework.stereotype.Service;

@Service
public class StockService {

    private final StockCatalogService stockCatalogService;
    private final HqaProperties properties;

    public StockService(StockCatalogService stockCatalogService, HqaProperties properties) {
        this.stockCatalogService = stockCatalogService;
        this.properties = properties;
    }

    public StockSearchResponse search(String query) {
        return stockCatalogService.search(query);
    }

    public RealtimePriceResponse getRealtimePrice(String stockCode) {
        stockCatalogService.validateCode(stockCode);
        if (properties.getKisAppKey().isBlank() || properties.getKisAppSecret().isBlank()) {
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503, "Realtime price API is not configured", null);
        }
        StockInfo stock = stockCatalogService.getStockInfo(stockCode);
        return new RealtimePriceResponse(
                stock,
                0,
                0,
                0.0,
                0,
                0,
                0,
                0L,
                null,
                null,
                null,
                OffsetDateTime.now()
        );
    }
}
