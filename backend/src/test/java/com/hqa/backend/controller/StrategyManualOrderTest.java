package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.dto.DirectBuyRequest;
import com.hqa.backend.entity.User;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.AutoTradeService;
import com.hqa.backend.service.HistoricalTradingSnapshotService;
import com.hqa.backend.service.KisClient;
import com.hqa.backend.service.TradeSignalService;
import jakarta.servlet.http.HttpSession;
import org.junit.jupiter.api.Test;

/** Direct buy/sell orders are refused for strategy accounts so the strategy ledger stays attributable. */
class StrategyManualOrderTest {

    @Test
    void manualOrdersAreRefusedForStrategyAccountsBeforeAnyKisCall() {
        AuthService auth = mock(AuthService.class);
        KisClient kis = mock(KisClient.class);
        StrategyAccountRepository strategyAccounts = mock(StrategyAccountRepository.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("u1");
        when(auth.requireUser(session)).thenReturn(user);
        when(strategyAccounts.existsById("u1")).thenReturn(true);
        TradingController controller = new TradingController(mock(AiServerClient.class), mock(AutoTradeService.class), auth, kis,
                mock(TradeSignalService.class), mock(HistoricalTradingSnapshotService.class), strategyAccounts);
        DirectBuyRequest request = new DirectBuyRequest();
        request.setStockName("삼성전자");
        request.setStockCode("005930");
        request.setQuantity(1);
        request.setLimitPrice(70_000);
        for (Runnable call : new Runnable[] {() -> controller.directBuy(request, session), () -> controller.directSell(request, session)}) {
            assertThatThrownBy(call::run).isInstanceOfSatisfying(ApiException.class, ex -> {
                assertThat(ex.getStatus()).isEqualTo(409);
                assertThat(ex.getMessage()).isEqualTo("STRATEGY_ACCOUNT_REJECTS_MANUAL_ORDERS");
            });
        }
        verifyNoInteractions(kis);
    }
}
