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
import org.springframework.test.util.ReflectionTestUtils;

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

    @Test
    void recentExplanationsCombinesSignalExecutionAndAgentReasons() {
        TradeSignal signal = new TradeSignal();
        ReflectionTestUtils.setField(signal, "id", "signal-1");
        signal.setUserId("user-1");
        signal.setSource("multi_theme_leader");
        signal.setStrategyProfile("short");
        signal.setThemeName("반도체");
        signal.setStockCode("005930");
        signal.setStockName("삼성전자");
        signal.setAction("BUY");
        signal.setLeaderScore(83);
        signal.setConfidence(76);
        signal.setRiskLevel("MEDIUM");
        signal.setPositionSize("5%");
        signal.setSignalPrice(72000L);
        signal.setStopLoss("-5%");
        signal.setReason("업황 회복과 수급 개선으로 조건부 매수");
        signal.setStatus("REJECTED");
        signal.setRejectReason("PRICE_DRIFT_EXCEEDED");
        signal.setRawPayload("""
                {
                  "leader": {
                    "analyst": {"summary": "반도체 업황 회복 기대", "total_score": 61, "hegemony_grade": "A"},
                    "quant": {"opinion": "재무 안정성 양호", "total_score": 78, "grade": "B+"},
                    "chartist": {"signal": "중립", "total_score": 64, "short_term_opinion": "단기 과열"},
                    "final_decision": {
                      "action_code": "BUY",
                      "summary": "낮은 비중으로 진입 가능",
                      "confidence": 76,
                      "risk_level_code": "MEDIUM",
                      "key_catalysts": ["수급 개선"],
                      "risk_factors": ["단기 과열"]
                    }
                  }
                }
                """);

        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId("signal-1");
        execution.setUserId("user-1");
        execution.setStatus("REJECTED");
        execution.setRejectReason("PRICE_DRIFT_EXCEEDED");
        execution.setQuantity(1);
        execution.setOrderPrice(74200L);
        execution.setCurrentPrice(74200L);
        execution.setPriceDriftPct(3.05);

        when(signalRepository.findTop100ByUserIdOrderByCreatedAtDesc("user-1")).thenReturn(List.of(signal));
        when(executionRepository.findBySignalIdOrderByExecutedAtDesc("signal-1")).thenReturn(List.of(execution));

        List<Map<String, Object>> explanations = service.recentExplanationsForUser("user-1", 10);

        assertThat(explanations).hasSize(1);
        Map<String, Object> item = explanations.get(0);
        assertThat(item).containsEntry("signalId", "signal-1");
        assertThat(item).containsEntry("stockName", "삼성전자");
        assertThat(item).containsEntry("action", "BUY");
        assertThat(item).containsEntry("reason", "업황 회복과 수급 개선으로 조건부 매수");
        assertThat(item).containsEntry("executionStatus", "REJECTED");
        assertThat(item).containsEntry("executionRejectReason", "PRICE_DRIFT_EXCEEDED");
        assertThat(item).containsEntry("currentPrice", 74200L);
        assertThat(item).containsEntry("priceDriftPct", 3.05);
        assertThat(item).containsEntry("explanationSummary", "업황 회복과 수급 개선으로 조건부 매수");
        assertThat((List<Object>) item.get("catalysts")).contains("수급 개선");
        assertThat((List<Object>) item.get("risks")).contains("단기 과열");
        assertThat((List<?>) item.get("agentReasons")).hasSize(4);
    }
}
