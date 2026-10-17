package com.hqa.backend.repository;

import com.hqa.backend.entity.StrategyAccount;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface StrategyAccountRepository extends JpaRepository<StrategyAccount, String> {
    Optional<StrategyAccount> findByStrategyId(String strategyId);
    List<StrategyAccount> findAllByOrderByStrategyIdAsc();
}
