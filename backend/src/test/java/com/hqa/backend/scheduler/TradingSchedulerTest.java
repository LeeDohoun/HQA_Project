package com.hqa.backend.scheduler;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import com.hqa.backend.service.TradeSignalService;
import org.junit.jupiter.api.Test;

class TradingSchedulerTest {

    @Test
    void fifteenMinuteRunOnlyCleansBackendSignalState() {
        TradeSignalService tradeSignalService = mock(TradeSignalService.class);
        TradingScheduler scheduler = new TradingScheduler(tradeSignalService);

        scheduler.run();

        verify(tradeSignalService).processPendingSignals();
    }

    @Test
    void submittedOrderRunProcessesSubmittedOrderExpirations() {
        TradeSignalService tradeSignalService = mock(TradeSignalService.class);
        TradingScheduler scheduler = new TradingScheduler(tradeSignalService);

        scheduler.processSubmittedOrders();

        verify(tradeSignalService).processSubmittedOrderExpirations();
    }
}
