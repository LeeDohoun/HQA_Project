package com.hqa.backend.repository;

import com.hqa.backend.entity.TradingHalt;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TradingHaltRepository extends JpaRepository<TradingHalt, String> {
    List<TradingHalt> findAllByOrderByScopeAsc();
}
