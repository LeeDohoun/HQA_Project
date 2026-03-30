package com.hqa.backend.repository;

import com.hqa.backend.entity.AnalysisRecord;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AnalysisRecordRepository extends JpaRepository<AnalysisRecord, String> {
}
