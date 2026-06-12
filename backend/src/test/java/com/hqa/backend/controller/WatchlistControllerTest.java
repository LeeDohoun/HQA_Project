package com.hqa.backend.controller;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.hqa.backend.dto.WatchlistItemRequest;
import com.hqa.backend.entity.User;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.WatchlistService;
import jakarta.servlet.http.HttpSession;
import org.junit.jupiter.api.Test;

class WatchlistControllerTest {

    @Test
    void listRequiresCurrentSessionUser() {
        AuthService authService = mock(AuthService.class);
        WatchlistService watchlistService = mock(WatchlistService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);

        WatchlistController controller = new WatchlistController(authService, watchlistService);

        controller.list(session);

        verify(authService).requireUser(session);
        verify(watchlistService).list(user);
    }

    @Test
    void addStoresItemForCurrentSessionUser() {
        AuthService authService = mock(AuthService.class);
        WatchlistService watchlistService = mock(WatchlistService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        WatchlistItemRequest request = new WatchlistItemRequest("삼성전자", "005930", "KOSPI");
        when(authService.requireUser(session)).thenReturn(user);

        WatchlistController controller = new WatchlistController(authService, watchlistService);

        controller.add(request, session);

        verify(watchlistService).add(user, request);
    }

    @Test
    void deleteRemovesItemForCurrentSessionUser() {
        AuthService authService = mock(AuthService.class);
        WatchlistService watchlistService = mock(WatchlistService.class);
        HttpSession session = mock(HttpSession.class);
        User user = new User();
        user.setUserId("user-1");
        when(authService.requireUser(session)).thenReturn(user);

        WatchlistController controller = new WatchlistController(authService, watchlistService);

        controller.delete("005930", session);

        verify(watchlistService).delete(user, "005930");
    }
}
