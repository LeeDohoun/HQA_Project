package com.hqa.backend.controller;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.hqa.backend.dto.AutoTradeToggleRequest;
import com.hqa.backend.entity.User;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.AutoTradeService;
import com.hqa.backend.service.HistoricalTradingSnapshotService;
import com.hqa.backend.service.KisClient;
import com.hqa.backend.service.TradeSignalService;
import jakarta.servlet.http.HttpSession;
import java.util.Map;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.Test;

class TradingControllerTest {

    @Test
    void ordersRequiresLoggedInUserSession() {
        AiServerClient aiServerClient = mock(AiServerClient.class);
        AuthService authService = mock(AuthService.class);
        HistoricalTradingSnapshotService snapshotService = mock(HistoricalTradingSnapshotService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);
        when(snapshotService.orders("user-1", null, 50)).thenReturn(Map.of("orders", java.util.List.of()));

        TradingController controller = new TradingController(
                aiServerClient,
                mock(AutoTradeService.class),
                authService,
                mock(KisClient.class),
                mock(TradeSignalService.class),
                snapshotService
        );

        controller.orders(null, 50, session);

        verify(authService).requireUser(session);
        verify(snapshotService).orders("user-1", null, 50);
        verify(aiServerClient, never()).getTradingOrders(null, 50);
    }

    @Test
    void explanationsReturnRecentAutoTradeReasonsForLoggedInUser() {
        AuthService authService = mock(AuthService.class);
        TradeSignalService tradeSignalService = mock(TradeSignalService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);
        when(tradeSignalService.recentExplanationsForUser("user-1", 5))
                .thenReturn(java.util.List.of(Map.of("stockName", "삼성전자", "action", "BUY")));

        TradingController controller = new TradingController(
                mock(AiServerClient.class),
                mock(AutoTradeService.class),
                authService,
                mock(KisClient.class),
                tradeSignalService,
                mock(HistoricalTradingSnapshotService.class)
        );

        Map<String, Object> response = controller.explanations(5, session);

        verify(authService).requireUser(session);
        verify(tradeSignalService).recentExplanationsForUser("user-1", 5);
        Assertions.assertEquals(1, ((java.util.List<?>) response.get("items")).size());
    }

    @Test
    void explanationsBoundLimitToFifty() {
        AuthService authService = mock(AuthService.class);
        TradeSignalService tradeSignalService = mock(TradeSignalService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);
        when(tradeSignalService.recentExplanationsForUser("user-1", 50))
                .thenReturn(java.util.List.of());

        TradingController controller = new TradingController(
                mock(AiServerClient.class),
                mock(AutoTradeService.class),
                authService,
                mock(KisClient.class),
                tradeSignalService,
                mock(HistoricalTradingSnapshotService.class)
        );

        controller.explanations(500, session);

        verify(tradeSignalService).recentExplanationsForUser("user-1", 50);
    }

    @Test
    void enablingAutoTradePersistsOnlyAuthenticatedUserForScheduler() {
        AiServerClient aiServerClient = mock(AiServerClient.class);
        AutoTradeService autoTradeService = mock(AutoTradeService.class);
        AuthService authService = mock(AuthService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);
        when(autoTradeService.setEnabled(user, true)).thenReturn(true);

        TradingController controller = new TradingController(
                aiServerClient,
                autoTradeService,
                authService,
                mock(KisClient.class),
                mock(TradeSignalService.class),
                mock(HistoricalTradingSnapshotService.class)
        );
        AutoTradeToggleRequest request = new AutoTradeToggleRequest();
        request.setEnabled(true);

        controller.toggleAuto(request, session);

        verifyNoInteractions(aiServerClient);
        verify(autoTradeService).setEnabled(user, true);
    }

    @Test
    void disablingAutoTradeDoesNotStopOtherUsersOrCallDeletedLoop() {
        AiServerClient aiServerClient = mock(AiServerClient.class);
        AutoTradeService autoTradeService = mock(AutoTradeService.class);
        AuthService authService = mock(AuthService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);
        when(autoTradeService.setEnabled(user, false)).thenReturn(false);

        TradingController controller = new TradingController(
                aiServerClient,
                autoTradeService,
                authService,
                mock(KisClient.class),
                mock(TradeSignalService.class),
                mock(HistoricalTradingSnapshotService.class)
        );
        AutoTradeToggleRequest request = new AutoTradeToggleRequest();
        request.setEnabled(false);

        controller.toggleAuto(request, session);

        verifyNoInteractions(aiServerClient);
        verify(autoTradeService).setEnabled(user, false);
    }

    @Test
    void enablingAutoTradePropagatesPaperAccountValidationFailure() {
        AiServerClient aiServerClient = mock(AiServerClient.class);
        AutoTradeService autoTradeService = mock(AutoTradeService.class);
        AuthService authService = mock(AuthService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);
        when(autoTradeService.setEnabled(user, true)).thenThrow(new IllegalStateException("PAPER_ACCOUNT_REQUIRED"));

        TradingController controller = new TradingController(
                aiServerClient,
                autoTradeService,
                authService,
                mock(KisClient.class),
                mock(TradeSignalService.class),
                mock(HistoricalTradingSnapshotService.class)
        );
        AutoTradeToggleRequest request = new AutoTradeToggleRequest();
        request.setEnabled(true);

        Assertions.assertThrows(IllegalStateException.class, () -> controller.toggleAuto(request, session));
        verifyNoInteractions(aiServerClient);
    }
}
