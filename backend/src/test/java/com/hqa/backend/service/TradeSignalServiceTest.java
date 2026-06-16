package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import com.hqa.backend.repository.UserRepository;
import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.test.util.ReflectionTestUtils;

class TradeSignalServiceTest {

    private final TradeSignalRepository signalRepository = mock(TradeSignalRepository.class);
    private final TradeSignalExecutionRepository executionRepository = mock(TradeSignalExecutionRepository.class);
    private final UserRepository userRepository = mock(UserRepository.class);
    private final KisClient kisClient = mock(KisClient.class);
    private final ErrorLogger errorLogger = mock(ErrorLogger.class);
    private final Clock marketOpenClock = Clock.fixed(Instant.parse("2026-06-16T01:00:00Z"), ZoneId.of("Asia/Seoul"));
    private final TradeSignalService service = new TradeSignalService(
            signalRepository,
            executionRepository,
            userRepository,
            kisClient,
            errorLogger,
            new ObjectMapper(),
            marketOpenClock
    );

    @Test
    void saveSignalStoresWaitingEntrySignalWithPlanAndConditions() {
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
                Map.of("leader", Map.of("leader_score", 82)),
                Map.of("strategy", "breakout"),
                Map.of("entry_conditions", List.of(Map.of("field", "current_price", "operator", ">=", "value", 72000))),
                "user-1:multi_theme_leader:short:005930:BUY:2026-06-01T10:15:00+09:00"
        );

        TradeSignal saved = service.saveSignal(request);

        assertThat(saved.getStatus()).isEqualTo("WAITING_ENTRY");
        assertThat(saved.getUserId()).isEqualTo("user-1");
        assertThat(saved.getStockCode()).isEqualTo("005930");
        assertThat(saved.getRawPayload()).contains("leader_score");
        assertThat(saved.getTradePlanJson()).contains("breakout");
        assertThat(saved.getConditionPayload()).contains("entry_conditions");
        assertThat(saved.getIdempotencyKey()).startsWith("user-1:multi_theme_leader");
    }

    @Test
    void saveSignalDeduplicatesByIdempotencyKey() {
        TradeSignal existing = new TradeSignal();
        existing.setUserId("user-1");
        existing.setSource("multi_theme_leader");
        existing.setStockCode("005930");
        existing.setStockName("삼성전자");
        existing.setAction("BUY");
        existing.setIdempotencyKey("idem-1");
        when(signalRepository.findByIdempotencyKey("idem-1")).thenReturn(Optional.of(existing));

        InternalTradeSignalRequest request = new InternalTradeSignalRequest(
                "user-1", "multi_theme_leader", "short", "ai", "AI",
                "005930", "삼성전자", "BUY", 82, 76, "LOW", "10%",
                72000L, "-5%", "조건부 매수", OffsetDateTime.now().plusMinutes(15),
                Map.of(), Map.of(), Map.of(), "idem-1"
        );

        TradeSignal saved = service.saveSignal(request);

        assertThat(saved).isSameAs(existing);
        verify(signalRepository, never()).save(any(TradeSignal.class));
    }

    @Test
    void hasSignalWithIdempotencyKeyReturnsTrueOnlyForStoredKey() {
        when(signalRepository.findByIdempotencyKey("idem-1")).thenReturn(Optional.of(new TradeSignal()));

        assertThat(service.hasSignalWithIdempotencyKey("idem-1")).isTrue();
        assertThat(service.hasSignalWithIdempotencyKey("")).isFalse();
    }

    @Test
    void triggerRejectsWhenUserAutoTradeIsOff() {
        TradeSignal signal = new TradeSignal();
        signal.setUserId("user-1");
        signal.setStockCode("005930");
        signal.setStockName("삼성전자");
        signal.setAction("BUY");
        signal.setStatus("WAITING_ENTRY");
        signal.setExpiresAt(OffsetDateTime.now().plusMinutes(10));
        User user = new User();
        user.setUserId("user-1");
        user.setAutoTradeEnabled(false);

        when(userRepository.findByUserId("user-1")).thenReturn(Optional.of(user));
        when(signalRepository.save(any(TradeSignal.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(executionRepository.save(any(TradeSignalExecution.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.triggerSignal(signal, Map.of("triggerType", "ENTRY"));

        assertThat(signal.getStatus()).isEqualTo("REJECTED");
        assertThat(signal.getRejectReason()).isEqualTo("AUTO_TRADE_DISABLED");
        verify(executionRepository).save(any(TradeSignalExecution.class));
    }

    @Test
    void triggerBuyRunsBackendGateAndRecordsSubmittedOrder() {
        TradeSignal signal = new TradeSignal();
        ReflectionTestUtils.setField(signal, "id", "sig-1");
        signal.setUserId("user-1");
        signal.setStockCode("005930");
        signal.setStockName("삼성전자");
        signal.setAction("BUY");
        signal.setStatus("WAITING_ENTRY");
        signal.setSignalPrice(72000L);
        signal.setExpiresAt(OffsetDateTime.now().plusMinutes(10));

        User user = new User();
        user.setUserId("user-1");
        user.setAutoTradeEnabled(true);
        UserSecret secret = new UserSecret();
        secret.setKisAppKey("app");
        secret.setKisAppSecret("secret");
        secret.setKisAccountNo("acct");
        user.setSecret(secret);

        when(userRepository.findByUserId("user-1")).thenReturn(Optional.of(user));
        when(kisClient.fetchAccessToken("user-1", secret)).thenReturn("token");
        when(kisClient.inquireBalance("user-1", secret, "token")).thenReturn(Map.of(
                "success", true,
                "summary", Map.of("deposit", 200000L),
                "holdings", List.of()
        ));
        when(kisClient.inquireCurrentPrice("user-1", secret, "token", "005930")).thenReturn(72100L);
        when(kisClient.buy("user-1", secret, "token", "005930", 1, 72100L))
                .thenReturn(Map.of(
                        "success", true,
                        "response", Map.of("output", Map.of("ODNO", "order-1"))
                ));
        when(signalRepository.save(any(TradeSignal.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(executionRepository.save(any(TradeSignalExecution.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.triggerSignal(signal, Map.of("triggerType", "ENTRY"));

        assertThat(signal.getStatus()).isEqualTo("ORDER_SUBMITTED");
        verify(kisClient).buy("user-1", secret, "token", "005930", 1, 72100L);
        ArgumentCaptor<TradeSignalExecution> execution = ArgumentCaptor.forClass(TradeSignalExecution.class);
        verify(executionRepository).save(execution.capture());
        assertThat(execution.getValue().getStatus()).isEqualTo("ORDER_SUBMITTED");
        assertThat(execution.getValue().getOrderId()).isEqualTo("order-1");
        assertThat(execution.getValue().getOrderType()).isEqualTo("LIMIT");
        assertThat(execution.getValue().getSubmittedQuantity()).isEqualTo(1);
        assertThat(execution.getValue().getOrderExpiresAt()).isNotNull();
    }

    @Test
    void completeSubmittedBuyOrderTransitionsSignalToOpen() {
        TradeSignal signal = new TradeSignal();
        ReflectionTestUtils.setField(signal, "id", "sig-1");
        signal.setUserId("user-1");
        signal.setAction("BUY");
        signal.setStatus("ORDER_SUBMITTED");

        TradeSignalExecution execution = new TradeSignalExecution();
        ReflectionTestUtils.setField(execution, "id", "exec-1");
        execution.setSignalId("sig-1");
        execution.setUserId("user-1");
        execution.setStatus("ORDER_SUBMITTED");
        execution.setSubmittedQuantity(1);
        execution.setOrderPrice(72100L);

        when(signalRepository.save(any(TradeSignal.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(executionRepository.save(any(TradeSignalExecution.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.completeSubmittedOrder(signal, execution, 1, 72100L);

        assertThat(signal.getStatus()).isEqualTo("OPEN");
        assertThat(execution.getStatus()).isEqualTo("OPEN");
        assertThat(execution.getFilledQuantity()).isEqualTo(1);
        assertThat(execution.getAverageFillPrice()).isEqualTo(72100L);
        assertThat(execution.getFilledAt()).isNotNull();
    }

    @Test
    void expireSubmittedOrderMarksSignalAndExecutionOrderExpired() {
        TradeSignal signal = new TradeSignal();
        ReflectionTestUtils.setField(signal, "id", "sig-1");
        signal.setUserId("user-1");
        signal.setAction("BUY");
        signal.setStatus("ORDER_SUBMITTED");

        TradeSignalExecution execution = new TradeSignalExecution();
        ReflectionTestUtils.setField(execution, "id", "exec-1");
        execution.setSignalId("sig-1");
        execution.setUserId("user-1");
        execution.setStatus("ORDER_SUBMITTED");
        execution.setSubmittedQuantity(1);
        execution.setFilledQuantity(0);

        when(signalRepository.save(any(TradeSignal.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(executionRepository.save(any(TradeSignalExecution.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.expireSubmittedOrder(signal, execution, "ORDER_NOT_FILLED");

        assertThat(signal.getStatus()).isEqualTo("ORDER_EXPIRED");
        assertThat(signal.getRejectReason()).isEqualTo("ORDER_NOT_FILLED");
        assertThat(execution.getStatus()).isEqualTo("ORDER_EXPIRED");
        assertThat(execution.getRejectReason()).isEqualTo("ORDER_NOT_FILLED");
    }

    @Test
    void completeSubmittedOrderKeepsPartialFillSeparateFromOpen() {
        TradeSignal signal = new TradeSignal();
        signal.setUserId("user-1");
        signal.setAction("BUY");
        signal.setStatus("ORDER_SUBMITTED");

        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId("sig-1");
        execution.setUserId("user-1");
        execution.setStatus("ORDER_SUBMITTED");
        execution.setSubmittedQuantity(3);

        when(signalRepository.save(any(TradeSignal.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(executionRepository.save(any(TradeSignalExecution.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.completeSubmittedOrder(signal, execution, 1, 72100L);

        assertThat(signal.getStatus()).isEqualTo("PARTIALLY_FILLED");
        assertThat(execution.getStatus()).isEqualTo("PARTIALLY_FILLED");
        assertThat(execution.getFilledQuantity()).isEqualTo(1);
    }

    @Test
    void processSubmittedOrderExpirationsExpiresOverdueSubmittedOrders() {
        TradeSignal signal = new TradeSignal();
        ReflectionTestUtils.setField(signal, "id", "sig-1");
        signal.setUserId("user-1");
        signal.setAction("BUY");
        signal.setStatus("ORDER_SUBMITTED");

        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId("sig-1");
        execution.setUserId("user-1");
        execution.setStatus("ORDER_SUBMITTED");
        execution.setOrderExpiresAt(OffsetDateTime.now(marketOpenClock).minusSeconds(1));

        when(executionRepository.findTop100ByStatusAndOrderExpiresAtBeforeOrderByOrderExpiresAtAsc(
                "ORDER_SUBMITTED", OffsetDateTime.now(marketOpenClock)
        )).thenReturn(List.of(execution));
        when(signalRepository.findById("sig-1")).thenReturn(Optional.of(signal));
        when(signalRepository.save(any(TradeSignal.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(executionRepository.save(any(TradeSignalExecution.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.processSubmittedOrderExpirations();

        assertThat(signal.getStatus()).isEqualTo("ORDER_EXPIRED");
        assertThat(execution.getStatus()).isEqualTo("ORDER_EXPIRED");
        assertThat(execution.getRejectReason()).isEqualTo("ORDER_NOT_FILLED");
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
        execution.setOrderId("order-1");
        execution.setOrderType("LIMIT");
        execution.setQuantity(1);
        execution.setSubmittedQuantity(1);
        execution.setFilledQuantity(0);
        execution.setOrderPrice(74200L);
        execution.setAverageFillPrice(0L);
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
        assertThat(item).containsEntry("orderId", "order-1");
        assertThat(item).containsEntry("orderType", "LIMIT");
        assertThat(item).containsEntry("submittedQuantity", 1);
        assertThat(item).containsEntry("filledQuantity", 0);
        assertThat(item).containsEntry("averageFillPrice", 0L);
        assertThat(item).containsEntry("currentPrice", 74200L);
        assertThat(item).containsEntry("priceDriftPct", 3.05);
        assertThat(item).containsEntry("explanationSummary", "업황 회복과 수급 개선으로 조건부 매수");
        assertThat((List<Object>) item.get("catalysts")).contains("수급 개선");
        assertThat((List<Object>) item.get("risks")).contains("단기 과열");
        assertThat((List<?>) item.get("agentReasons")).hasSize(4);
    }
}
