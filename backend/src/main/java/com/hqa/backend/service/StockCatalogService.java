package com.hqa.backend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.StockInfo;
import com.hqa.backend.dto.StockSearchResponse;
import com.hqa.backend.dto.StockSearchResult;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.dto.ErrorCode;
import java.io.InputStream;
import jakarta.annotation.PostConstruct;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
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
        try (InputStream inputStream = getClass().getClassLoader().getResourceAsStream("watchlist.json")) {
            if (inputStream == null) {
                loadFallbackStocks();
                return;
            }
            JsonNode root = objectMapper.readTree(inputStream);
            for (JsonNode node : root.path("stocks")) {
                stocks.add(new StockSearchResult(
                        node.path("name").asText(),
                        node.path("code").asText(),
                        node.path("market").asText("KRX")
                ));
            }
        } catch (IOException ignored) {
            loadFallbackStocks();
        }
    }

    public StockSearchResponse search(String query) {
        String normalized = query.trim().toLowerCase(Locale.ROOT);
        String[] terms = Arrays.stream(normalized.split("[,\\s]+"))
                .map(String::trim)
                .filter(term -> !term.isBlank())
                .toArray(String[]::new);

        if (normalized.matches("^\\d{6}$")) {
            return new StockSearchResponse(
                    stocks.stream().filter(stock -> stock.code().equals(normalized)).limit(1).toList(),
                    stocks.stream().anyMatch(stock -> stock.code().equals(normalized)) ? 1 : 0
            );
        }

        List<StockSearchResult> results = stocks.stream()
                .filter(stock -> matches(stock, terms))
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

    private boolean matches(StockSearchResult stock, String[] terms) {
        String name = stock.name().toLowerCase(Locale.ROOT);
        String code = stock.code().toLowerCase(Locale.ROOT);
        String market = stock.market() == null ? "" : stock.market().toLowerCase(Locale.ROOT);

        for (String term : terms) {
            if (!(name.contains(term) || code.contains(term) || market.contains(term))) {
                return false;
            }
        }
        return true;
    }

    private void loadFallbackStocks() {
        if (!stocks.isEmpty()) {
            return;
        }
        stocks.add(new StockSearchResult("Samsung Electronics", "005930", "KOSPI"));
        stocks.add(new StockSearchResult("SK hynix", "000660", "KOSPI"));
        stocks.add(new StockSearchResult("NAVER", "035420", "KOSPI"));
        stocks.add(new StockSearchResult("Kakao", "035720", "KOSPI"));
        stocks.add(new StockSearchResult("LG Energy Solution", "373220", "KOSPI"));
    }
}
