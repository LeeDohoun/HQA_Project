package com.hqa.backend.scheduler;

import com.hqa.backend.service.TradeSignalService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 백엔드 주문 상태 정리를 수행한다.
 */
@Component
public class TradingScheduler {

    private static final Logger log = LoggerFactory.getLogger(TradingScheduler.class);

    private final TradeSignalService tradeSignalService;

    public TradingScheduler(TradeSignalService tradeSignalService) {
        this.tradeSignalService = tradeSignalService;
    }

    @Scheduled(fixedRate = 900_000)
    public void run() {
        try {
            tradeSignalService.processPendingSignals();
        } catch (Exception e) {
            log.error("[TradingScheduler] pending signal processing failed: {}", e.getMessage());
        }
    }

    @Scheduled(fixedRate = 60_000)
    public void processSubmittedOrders() {
        try {
            tradeSignalService.processSubmittedOrderExpirations();
        } catch (Exception e) {
            log.error("[TradingScheduler] submitted order processing failed: {}", e.getMessage());
        }
    }
}
