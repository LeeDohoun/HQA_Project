package com.hqa.backend.service;

import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.StockInfo;
import com.hqa.backend.dto.StockSearchResponse;
import com.hqa.backend.dto.StockSearchResult;
import com.hqa.backend.entity.Stock;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.StockRepository;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

/**
 * Search facade over {@code stocks}. Backed by KIS daily master files
 * loaded by {@link KisMasterLoader} / {@code StockScheduler}.
 */
@Service
public class StockCatalogService {

    private static final int MAX_RESULTS = 10;

    private final StockRepository repository;

    public StockCatalogService(StockRepository repository) {
        this.repository = repository;
    }

    public StockSearchResponse search(String query) {
        String term = query == null ? "" : query.trim();
        if (term.isEmpty()) {
            return new StockSearchResponse(List.of(), 0);
        }

        if (term.matches("^\\d{6}$")) {
            Optional<Stock> exact = repository.findByCode(term);
            return exact
                    .map(stock -> new StockSearchResponse(List.of(toResult(stock)), 1))
                    .orElseGet(() -> new StockSearchResponse(List.of(), 0));
        }

        List<StockSearchResult> results = repository
                .searchByTerm(term.toLowerCase(Locale.ROOT), PageRequest.of(0, MAX_RESULTS))
                .stream()
                .map(this::toResult)
                .toList();
        return new StockSearchResponse(results, results.size());
    }

    public StockInfo getStockInfo(String code) {
        return repository.findByCode(code)
                .map(stock -> new StockInfo(displayName(stock), stock.getCode()))
                .orElseGet(() -> new StockInfo(code, code));
    }

    public void validateCode(String code) {
        if (code == null || !code.matches("^\\d{6}$")) {
            throw new ApiException(ErrorCode.STOCK_INVALID_CODE, 400, "Stock code must be 6 digits", null);
        }
    }

    private StockSearchResult toResult(Stock stock) {
        return new StockSearchResult(displayName(stock), stock.getCode(), stock.getMarket());
    }

    private String displayName(Stock stock) {
        if (stock.getNameKo() != null && !stock.getNameKo().isBlank()) return stock.getNameKo();
        if (stock.getNameEn() != null && !stock.getNameEn().isBlank()) return stock.getNameEn();
        return stock.getCode();
    }
}
