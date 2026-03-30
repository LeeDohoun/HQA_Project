package com.hqa.backend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.StockInfo;
import com.hqa.backend.dto.StockSearchResponse;
import com.hqa.backend.dto.StockSearchResult;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.dto.ErrorCode;
import jakarta.annotation.PostConstruct;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import org.springframework.stereotype.Service;

@Service
public class StockCatalogService {

    private final ObjectMapper objectMapper;
    private final List<StockSearchResult> stocks = new ArrayList<>();

    public StockCatalogService(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @PostConstruct
    public void load() {
        Path path = Path.of("data", "watchlist.json");
        if (!Files.exists(path)) {
            return;
        }
        try {
            JsonNode root = objectMapper.readTree(Files.readString(path));
            for (JsonNode node : root.path("stocks")) {
                stocks.add(new StockSearchResult(node.path("name").asText(), node.path("code").asText(), null));
            }
        } catch (IOException ignored) {
        }
    }

    public StockSearchResponse search(String query) {
        if (query.matches("^\\d{6}$")) {
            return new StockSearchResponse(
                    stocks.stream().filter(stock -> stock.code().equals(query)).limit(1).toList(),
                    stocks.stream().anyMatch(stock -> stock.code().equals(query)) ? 1 : 0
            );
        }

        String normalized = query.toLowerCase(Locale.ROOT);
        List<StockSearchResult> results = stocks.stream()
                .filter(stock -> stock.name().toLowerCase(Locale.ROOT).contains(normalized)
                        || stock.code().contains(normalized))
                .limit(10)
                .toList();
        return new StockSearchResponse(results, results.size());
    }

    public StockInfo getStockInfo(String code) {
        Optional<StockSearchResult> found = stocks.stream().filter(stock -> stock.code().equals(code)).findFirst();
        return found.map(stock -> new StockInfo(stock.name(), stock.code()))
                .orElseGet(() -> new StockInfo(code, code));
    }

    public void validateCode(String code) {
        if (!code.matches("^\\d{6}$")) {
            throw new ApiException(ErrorCode.STOCK_INVALID_CODE, 400, "Stock code must be 6 digits", null);
        }
    }
}
