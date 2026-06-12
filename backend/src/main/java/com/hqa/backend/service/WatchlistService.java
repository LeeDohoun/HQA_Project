package com.hqa.backend.service;

import com.hqa.backend.dto.WatchlistItemRequest;
import com.hqa.backend.dto.WatchlistItemResponse;
import com.hqa.backend.dto.WatchlistResponse;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.WatchlistItem;
import com.hqa.backend.repository.WatchlistItemRepository;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional
public class WatchlistService {

    private final WatchlistItemRepository repository;

    public WatchlistService(WatchlistItemRepository repository) {
        this.repository = repository;
    }

    @Transactional(readOnly = true)
    public WatchlistResponse list(User user) {
        List<WatchlistItemResponse> items = repository.findByUserOrderByCreatedAtDesc(user)
                .stream()
                .map(this::toResponse)
                .toList();
        return new WatchlistResponse(items, items.size());
    }

    public WatchlistItemResponse add(User user, WatchlistItemRequest request) {
        String stockCode = request.stockCode().trim();
        WatchlistItem item = repository.findByUserAndStockCode(user, stockCode)
                .orElseGet(WatchlistItem::new);
        item.setUser(user);
        item.setStockCode(stockCode);
        item.setStockName(request.stockName().trim());
        item.setMarket(normalizeMarket(request.market()));
        return toResponse(repository.save(item));
    }

    public void delete(User user, String stockCode) {
        repository.deleteByUserAndStockCode(user, stockCode);
    }

    private WatchlistItemResponse toResponse(WatchlistItem item) {
        return new WatchlistItemResponse(
                item.getId(),
                item.getStockName(),
                item.getStockCode(),
                item.getMarket(),
                item.getCreatedAt(),
                item.getUpdatedAt()
        );
    }

    private static String normalizeMarket(String market) {
        if (market == null || market.isBlank()) {
            return "KR";
        }
        return market.trim();
    }
}
