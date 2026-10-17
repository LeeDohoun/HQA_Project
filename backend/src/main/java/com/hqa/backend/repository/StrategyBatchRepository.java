package com.hqa.backend.repository;

import com.hqa.backend.entity.StrategyBatch;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface StrategyBatchRepository extends JpaRepository<StrategyBatch, UUID> {
    Optional<StrategyBatch> findByIdempotencyKey(String idempotencyKey);
    List<StrategyBatch> findBySessionDateAndStatusInOrderByCreatedAtAsc(LocalDate sessionDate, List<String> statuses);
    Optional<StrategyBatch> findFirstByStrategyIdOrderByCreatedAtDesc(String strategyId);
    boolean existsByUserIdAndStatusIn(String userId, List<String> statuses);
    boolean existsByUserIdAndSessionDateAndPhaseAndStatusNot(String userId, LocalDate sessionDate, String phase, String status);
    boolean existsByUserIdAndSessionDateAndStatusIn(String userId, LocalDate sessionDate, List<String> statuses);

    /** Batches of past sessions that are unfinished or still hold orders that may be open at the broker. */
    @Query("SELECT b FROM StrategyBatch b WHERE b.sessionDate < :day AND (b.status IN ('QUEUED', 'SUBMITTING', 'SUBMITTED') "
            + "OR EXISTS (SELECT o.id FROM StrategyBatchOrder o WHERE o.batchId = b.id "
            + "AND o.status IN ('PENDING', 'SUBMITTED', 'PARTIALLY_FILLED'))) ORDER BY b.sessionDate ASC, b.createdAt ASC")
    List<StrategyBatch> findStaleBefore(LocalDate day);
}
