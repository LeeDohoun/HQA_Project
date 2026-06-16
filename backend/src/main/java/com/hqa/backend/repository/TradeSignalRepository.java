package com.hqa.backend.repository;

import com.hqa.backend.entity.TradeSignal;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TradeSignalRepository extends JpaRepository<TradeSignal, String> {
    List<TradeSignal> findTop100ByStatusOrderByCreatedAtAsc(String status);
    List<TradeSignal> findTop100ByUserIdOrderByCreatedAtDesc(String userId);
    List<TradeSignal> findTop100ByOrderByCreatedAtDesc();
    Optional<TradeSignal> findByIdempotencyKey(String idempotencyKey);
}
