package com.hqa.backend.controller;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.hqa.backend.entity.User;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.AutoTradeService;
import com.hqa.backend.service.KisClient;
import com.hqa.backend.service.TradeSignalService;
import jakarta.servlet.http.HttpSession;
import java.util.Map;
import org.junit.jupiter.api.Test;

class TradingControllerTest {

    @Test
    void ordersRequiresLoggedInUserSession() {
        AiServerClient aiServerClient = mock(AiServerClient.class);
        AuthService authService = mock(AuthService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);
        when(aiServerClient.getTradingOrders(null, 50)).thenReturn(Map.of("items", java.util.List.of()));

        TradingController controller = new TradingController(
                aiServerClient,
                mock(AutoTradeService.class),
                authService,
                mock(KisClient.class),
                mock(TradeSignalService.class)
        );

        controller.orders(null, 50, session);

        verify(authService).requireUser(session);
    }
}
