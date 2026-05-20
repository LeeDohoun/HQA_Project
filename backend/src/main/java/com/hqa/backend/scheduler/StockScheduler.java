package com.hqa.backend.scheduler;

import com.hqa.backend.repository.StockRepository;
import com.hqa.backend.service.KisMasterLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Keeps {@code stocks} populated from KIS's daily master files.
 *
 * - On startup: if the table is empty, fetch immediately so the app is usable
 *   on first boot without waiting for the next 07:30 tick.
 * - Daily 07:30 KST: refresh before market open so newly listed / delisted
 *   stocks are reflected in search.
 */
@Component
public class StockScheduler {

    private static final Logger log = LoggerFactory.getLogger(StockScheduler.class);

    private final StockRepository repository;
    private final KisMasterLoader loader;

    public StockScheduler(StockRepository repository, KisMasterLoader loader) {
        this.repository = repository;
        this.loader = loader;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void loadOnStartupIfEmpty() {
        long existing = repository.count();
        if (existing > 0) {
            log.info("[StockScheduler] stocks has {} rows — skipping startup reload", existing);
            return;
        }
        log.info("[StockScheduler] stocks empty — pulling KIS master on startup");
        try {
            loader.reload();
        } catch (Exception e) {
            log.error("[StockScheduler] startup load failed: {}", e.toString());
        }
    }

    @Scheduled(cron = "0 30 7 * * *", zone = "Asia/Seoul")
    public void refreshDaily() {
        log.info("[StockScheduler] daily refresh tick (07:30 KST)");
        try {
            loader.reload();
        } catch (Exception e) {
            log.error("[StockScheduler] daily refresh failed: {}", e.toString());
        }
    }
}
