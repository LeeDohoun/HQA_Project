package com.hqa.backend.repository;

import com.hqa.backend.entity.TradeSignalExecution;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TradeSignalExecutionRepository extends JpaRepository<TradeSignalExecution, String> {
    List<TradeSignalExecution> findBySignalId(String signalId);
    List<TradeSignalExecution> findBySignalIdOrderByExecutedAtDesc(String signalId);
}
