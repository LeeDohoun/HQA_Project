package com.hqa.backend.repository;

import com.hqa.backend.entity.ErrorLog;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ErrorLogRepository extends JpaRepository<ErrorLog, String> {
}
