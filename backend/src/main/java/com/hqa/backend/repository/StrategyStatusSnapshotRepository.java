package com.hqa.backend.repository;

import com.hqa.backend.entity.StrategyStatusSnapshot;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface StrategyStatusSnapshotRepository extends JpaRepository<StrategyStatusSnapshot, String> {
    List<StrategyStatusSnapshot> findAllByOrderByStrategyIdAsc();
}
