package com.hqa.backend.repository;

import com.hqa.backend.entity.TradeSignal;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface TradeSignalRepository extends JpaRepository<TradeSignal, String> {
    List<TradeSignal> findTop100ByStatusOrderByCreatedAtAsc(String status);
    List<TradeSignal> findTop100ByUserIdOrderByCreatedAtDesc(String userId);
    List<TradeSignal> findTop100ByOrderByCreatedAtDesc();
    Optional<TradeSignal> findByIdempotencyKey(String idempotencyKey);
    Page<TradeSignal> findByStatusIn(List<String> statuses, Pageable pageable);
    List<TradeSignal> findByUserIdAndStatusIn(String userId, List<String> statuses);
    @Query("SELECT s.userId FROM TradeSignal s WHERE s.id = :id")
    Optional<String> ownerOf(String id);
}
