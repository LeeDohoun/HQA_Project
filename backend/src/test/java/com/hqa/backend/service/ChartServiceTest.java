package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.hqa.backend.dto.CandleData;
import com.hqa.backend.dto.CandleHistoryResponse;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.exception.ApiException;
import jakarta.servlet.http.HttpSession;
import java.time.LocalDate;
import java.util.List;
import org.junit.jupiter.api.Test;

class ChartServiceTest {

    @Test
    void fallsBackToLocalDailyCandlesWhenKisReturnsNoDailyData() {
        AuthService authService = mock(AuthService.class);
        KisClient kisClient = mock(KisClient.class);
        LocalChartDataService localChartDataService = mock(LocalChartDataService.class);
        HttpSession session = mock(HttpSession.class);

        User user = new User();
        user.setUserId("user-1");
        user.setSecret(new UserSecret());

        when(authService.requireUser(session)).thenReturn(user);
        when(kisClient.fetchAccessToken("user-1", user.getSecret())).thenReturn("token-1");
        when(kisClient.fetchDailyCandles(eq("user-1"), eq(user.getSecret()), eq("token-1"), eq("005930"),
                eq("D"), any(LocalDate.class), any(LocalDate.class))).thenReturn(List.of());
        when(localChartDataService.fetchDailyCandles("005930", "1d", 200, null)).thenReturn(List.of(
                new CandleData(1767222000L, 70000, 71000, 69000, 70500, 1000, true),
                new CandleData(1767308400L, 70500, 72000, 70000, 71500, 1200, true)
        ));

        ChartService chartService = new ChartService(authService, kisClient, localChartDataService);

        CandleHistoryResponse response = chartService.getHistoricalCandles(
                "005930", "1d", 200, null, session);

        assertThat(response.candles()).hasSize(2);
        assertThat(response.candles().get(1).close()).isEqualTo(71500);
        assertThat(response.hasMore()).isFalse();
    }

    @Test
    void usesKisDailyCandlesForDailyTimeframes() {
        AuthService authService = mock(AuthService.class);
        KisClient kisClient = mock(KisClient.class);
        LocalChartDataService localChartDataService = mock(LocalChartDataService.class);
        HttpSession session = mock(HttpSession.class);

        User user = new User();
        user.setUserId("user-1");
        user.setSecret(new UserSecret());

        when(authService.requireUser(session)).thenReturn(user);
        when(kisClient.fetchAccessToken("user-1", user.getSecret())).thenReturn("token-1");
        when(kisClient.fetchDailyCandles(eq("user-1"), eq(user.getSecret()), eq("token-1"), eq("005930"),
                eq("D"), any(LocalDate.class), any(LocalDate.class))).thenReturn(List.of(
                new CandleData(1767222000L, 70000, 71000, 69000, 70500, 1000, true)
        ));

        ChartService chartService = new ChartService(authService, kisClient, localChartDataService);

        CandleHistoryResponse response = chartService.getHistoricalCandles(
                "005930", "1d", 200, null, session);

        assertThat(response.candles()).hasSize(1);
        assertThat(response.candles().get(0).close()).isEqualTo(70500);
    }

    @Test
    void requiresKisCredentialsForDailyTimeframes() {
        AuthService authService = mock(AuthService.class);
        KisClient kisClient = mock(KisClient.class);
        LocalChartDataService localChartDataService = mock(LocalChartDataService.class);
        HttpSession session = mock(HttpSession.class);

        User user = new User();
        user.setUserId("user-1");

        when(authService.requireUser(session)).thenReturn(user);

        ChartService chartService = new ChartService(authService, kisClient, localChartDataService);

        assertThatThrownBy(() -> chartService.getHistoricalCandles("005930", "1d", 200, null, session))
                .isInstanceOf(ApiException.class)
                .extracting(e -> ((ApiException) e).getErrorCode())
                .isEqualTo(ErrorCode.KIS_SECRET_NOT_CONFIGURED);
    }
}
