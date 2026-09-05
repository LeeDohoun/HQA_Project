package com.hqa.backend.repository;

import com.hqa.backend.entity.TradeSignalExecution;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface TradeSignalExecutionRepository extends JpaRepository<TradeSignalExecution, String> {
    List<TradeSignalExecution> findBySignalId(String signalId);
    List<TradeSignalExecution> findBySignalIdOrderByExecutedAtDesc(String signalId);
    List<TradeSignalExecution> findTop100ByStatusAndOrderExpiresAtBeforeOrderByOrderExpiresAtAsc(String status, OffsetDateTime now);
    boolean existsBySignalIdAndStatus(String signalId, String status);
    long countByUserIdAndExecutedAtAfter(String userId, OffsetDateTime executedAt);
    Optional<TradeSignalExecution> findByTriggerKey(String triggerKey);
    List<TradeSignalExecution> findByStatusInOrderBySubmittedAtAsc(List<String> statuses);
    List<TradeSignalExecution> findByUserIdAndStatusIn(String userId, List<String> statuses);
    @Query("SELECT COALESCE(SUM(e.reservedCash),0) FROM TradeSignalExecution e WHERE e.userId = :userId")
    long reservedCashForUser(String userId);
    long countByUserIdAndOrderSideAndSubmittedAtAfter(String userId, String orderSide, OffsetDateTime after);
    @Query("SELECT e.userId FROM TradeSignalExecution e WHERE e.id = :id")
    Optional<String> ownerOf(String id);
}
