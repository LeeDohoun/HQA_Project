package com.hqa.backend.repository;

import com.hqa.backend.entity.StrategyBatchSizing;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface StrategyBatchSizingRepository extends JpaRepository<StrategyBatchSizing, UUID> {
}
