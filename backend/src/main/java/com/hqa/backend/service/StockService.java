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
    private final KisClient kisClient;

    public StockService(StockCatalogService stockCatalogService, AuthService authService, KisClient kisClient) {
        this.stockCatalogService = stockCatalogService;
        this.authService = authService;
        this.kisClient = kisClient;
    }

    public StockSearchResponse search(String query) {
        return stockCatalogService.search(query);
    }

    public RealtimePriceResponse getRealtimePrice(String stockCode, HttpSession session) {
        stockCatalogService.validateCode(stockCode);
        var user = authService.requireUser(session);
        var secret = authService.requireUserSecret(session);
        StockInfo stock = stockCatalogService.getStockInfo(stockCode);
        String token = kisClient.fetchAccessToken(user.getUserId(), secret);
        Long current = token == null ? null : kisClient.inquireCurrentPrice(user.getUserId(), secret, token, stockCode);
        return new RealtimePriceResponse(
                stock,
                current == null ? 0 : current.intValue(),
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
