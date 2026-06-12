package com.hqa.backend.repository;

import com.hqa.backend.entity.User;
import com.hqa.backend.entity.WatchlistItem;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface WatchlistItemRepository extends JpaRepository<WatchlistItem, String> {
    List<WatchlistItem> findByUserOrderByCreatedAtDesc(User user);

    Optional<WatchlistItem> findByUserAndStockCode(User user, String stockCode);

    void deleteByUserAndStockCode(User user, String stockCode);
}
