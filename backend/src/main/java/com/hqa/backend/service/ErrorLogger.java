package com.hqa.backend.service;

import com.hqa.backend.entity.ErrorLog;
import com.hqa.backend.repository.ErrorLogRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ErrorLogger {

    private static final Logger log = LoggerFactory.getLogger(ErrorLogger.class);

    private final ErrorLogRepository repository;

    public ErrorLogger(ErrorLogRepository repository) {
        this.repository = repository;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void log(String source, String userId, String stockCode, String message, String detail) {
        log.error("[{}] userId={} stockCode={} message={}", source, userId, stockCode, message);
        try {
            ErrorLog entry = new ErrorLog();
            entry.setSource(source);
            entry.setUserId(userId);
            entry.setStockCode(stockCode);
            entry.setMessage(message);
            entry.setDetail(detail);
            repository.save(entry);
        } catch (Exception e) {
            log.error("[ErrorLogger] Failed to persist error log: {}", e.getMessage());
        }
    }
}
