package com.hqa.backend.repository;

import com.hqa.backend.entity.StrategyBatchOrder;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface StrategyBatchOrderRepository extends JpaRepository<StrategyBatchOrder, UUID> {
    List<StrategyBatchOrder> findByBatchIdOrderBySeqAsc(UUID batchId);
    List<StrategyBatchOrder> findByStatusOrderBySubmittedAtAsc(String status);
    List<StrategyBatchOrder> findByOrderDateAndBrokerOrderIdIsNotNull(LocalDate orderDate);

    @Query("SELECT COUNT(o) > 0 FROM StrategyBatchOrder o, StrategyBatch b WHERE o.batchId = b.id "
            + "AND b.userId = :userId AND o.status IN :statuses")
    boolean existsOrderForUserWithStatusIn(String userId, List<String> statuses);

    /** Orders of the account's stored batches for the session, except those skipped without sending. */
    @Query("SELECT COUNT(o) FROM StrategyBatchOrder o, StrategyBatch b WHERE o.batchId = b.id "
            + "AND b.userId = :userId AND b.sessionDate = :day AND o.status <> 'SKIPPED'")
    long countOrdersForUserOn(String userId, LocalDate day);

    @Query("SELECT COUNT(o) FROM StrategyBatchOrder o, StrategyBatch b WHERE o.batchId = b.id "
            + "AND b.userId = :userId AND b.sessionDate = :day AND o.cancelRequestedAt IS NOT NULL")
    long countCancelsForUserOn(String userId, LocalDate day);
}
