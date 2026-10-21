package com.hqa.backend.repository;

import com.hqa.backend.entity.StrategyNavClose;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface StrategyNavCloseRepository extends JpaRepository<StrategyNavClose, StrategyNavClose.Key> {
    Optional<StrategyNavClose> findByAccountBindingAndSessionDateAndSource(String accountBinding, LocalDate sessionDate,
            String source);
    List<StrategyNavClose> findByAccountBindingAndSessionDateGreaterThanEqualOrderBySessionDateAsc(String accountBinding,
            LocalDate from);
}
