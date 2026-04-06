package com.hqa.backend.service;

import com.hqa.backend.dto.RealtimePriceResponse;
import com.hqa.backend.dto.StockInfo;
import com.hqa.backend.dto.StockSearchResponse;
import jakarta.servlet.http.HttpSession;
import java.time.OffsetDateTime;
import org.springframework.stereotype.Service;

@Service
public class StockService {

    private final StockCatalogService stockCatalogService;
    private final AuthService authService;

    public StockService(StockCatalogService stockCatalogService, AuthService authService) {
        this.stockCatalogService = stockCatalogService;
        this.authService = authService;
    }

    public StockSearchResponse search(String query) {
        return stockCatalogService.search(query);
    }

    public RealtimePriceResponse getRealtimePrice(String stockCode, HttpSession session) {
        stockCatalogService.validateCode(stockCode);
        authService.requireUserSecret(session);
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
