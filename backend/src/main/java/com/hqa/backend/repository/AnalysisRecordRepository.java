package com.hqa.backend.repository;

import com.hqa.backend.entity.AnalysisRecord;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.transaction.annotation.Transactional;

public interface AnalysisRecordRepository extends JpaRepository<AnalysisRecord, String> {
    java.util.Optional<AnalysisRecord> findByTaskIdAndUser_Id(String taskId, String userId);
    org.springframework.data.domain.Page<AnalysisRecord> findByUser_IdOrderByCreatedAtDesc(
            String userId, org.springframework.data.domain.Pageable pageable);

    @Modifying
    @Transactional
    @Query("update AnalysisRecord a set a.status = 'running' where a.taskId = :taskId and a.user.id = :userId "
            + "and a.status = 'pending' and a.resultJson is null")
    int markRunning(String taskId, String userId);

    @Modifying
    @Transactional
    @Query("update AnalysisRecord a set a.status = :status, a.stockName = :stockName, "
            + "a.completedAt = :completedAt, a.resultJson = :resultJson "
            + "where a.taskId = :taskId and a.user.id = :userId and a.resultJson is null")
    int storeResultIfAbsent(String taskId, String userId, String status, String stockName,
            java.time.OffsetDateTime completedAt, String resultJson);
}
