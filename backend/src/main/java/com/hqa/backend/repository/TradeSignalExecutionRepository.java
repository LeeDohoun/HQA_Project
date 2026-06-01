package com.hqa.backend.repository;

import com.hqa.backend.entity.TradeSignalExecution;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TradeSignalExecutionRepository extends JpaRepository<TradeSignalExecution, String> {
}
