package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

class TradeSignalServiceTest {
    private final TradeSignalRepository signalRepository = mock(TradeSignalRepository.class);
    private final TradeSignalExecutionRepository executionRepository = mock(TradeSignalExecutionRepository.class);
    private final PaperTradeLifecycle lifecycle = mock(PaperTradeLifecycle.class);
    private final TradeSignalService service = new TradeSignalService(signalRepository, executionRepository, new ObjectMapper(), lifecycle);

    @Test
    void historicPlanReceiptIsUsedAfterReplacement() {
        when(lifecycle.hasReceipt("analysis-a")).thenReturn(true);
        assertThat(service.hasSignalWithIdempotencyKey("analysis-a")).isTrue();
        assertThat(service.hasSignalWithIdempotencyKey("")).isFalse();
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
