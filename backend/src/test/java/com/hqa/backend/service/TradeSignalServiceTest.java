package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import com.hqa.backend.repository.UserRepository;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class TradeSignalServiceTest {

    private final TradeSignalRepository signalRepository = mock(TradeSignalRepository.class);
    private final TradeSignalExecutionRepository executionRepository = mock(TradeSignalExecutionRepository.class);
    private final UserRepository userRepository = mock(UserRepository.class);
    private final KisClient kisClient = mock(KisClient.class);
    private final ErrorLogger errorLogger = mock(ErrorLogger.class);
    private final TradeSignalService service = new TradeSignalService(
            signalRepository,
            executionRepository,
            userRepository,
            kisClient,
            errorLogger,
            new ObjectMapper()
    );

    @Test
    void saveSignalStoresPendingSignal() {
        when(signalRepository.save(any(TradeSignal.class))).thenAnswer(invocation -> invocation.getArgument(0));
        InternalTradeSignalRequest request = new InternalTradeSignalRequest(
                "user-1",
                "multi_theme_leader",
                "short",
                "ai",
                "AI",
                "005930",
                "삼성전자",
                "BUY",
                82,
                76,
                "LOW",
                "10%",
                72000L,
                "-5%",
                "조건부 매수",
                OffsetDateTime.now().plusMinutes(15),
                Map.of("leader", Map.of("leader_score", 82))
        );

        TradeSignal saved = service.saveSignal(request);

        assertThat(saved.getStatus()).isEqualTo("PENDING");
        assertThat(saved.getUserId()).isEqualTo("user-1");
        assertThat(saved.getStockCode()).isEqualTo("005930");
        assertThat(saved.getRawPayload()).contains("leader_score");
    }

    @Test
    void processPendingRejectsWhenUserAutoTradeIsOff() {
        TradeSignal signal = new TradeSignal();
        signal.setUserId("user-1");
        signal.setStockCode("005930");
        signal.setStockName("삼성전자");
        signal.setAction("BUY");
        signal.setStatus("PENDING");
        signal.setExpiresAt(OffsetDateTime.now().plusMinutes(10));
        User user = new User();
        user.setUserId("user-1");
        user.setAutoTradeEnabled(false);

        when(signalRepository.findTop100ByStatusOrderByCreatedAtAsc("PENDING")).thenReturn(List.of(signal));
        when(userRepository.findByUserId("user-1")).thenReturn(Optional.of(user));
        when(signalRepository.save(any(TradeSignal.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(executionRepository.save(any(TradeSignalExecution.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.processPendingSignals();

        assertThat(signal.getStatus()).isEqualTo("REJECTED");
        assertThat(signal.getRejectReason()).isEqualTo("AUTO_TRADE_DISABLED");
        verify(executionRepository).save(any(TradeSignalExecution.class));
    }
}
