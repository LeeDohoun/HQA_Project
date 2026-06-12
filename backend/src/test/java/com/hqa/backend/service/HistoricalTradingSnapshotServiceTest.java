package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.test.util.ReflectionTestUtils;

class HistoricalTradingSnapshotServiceTest {

    @TempDir
    Path tempDir;

    @Test
    void aiActivityUsesLatestReportWithRankedLeaders() throws Exception {
        Path reports = Files.createDirectories(tempDir.resolve("reports"));
        Files.writeString(reports.resolve("multi_theme_leader_trading_preview_20260612-103346.json"), """
                {"mode":"preview","executed_at":"2026-06-12T10:33:46+09:00","global_ranked_leaders":[]}
                """);
        Files.writeString(reports.resolve("multi_theme_leader_trading_preview_20260603-201215.json"), """
                {
                  "mode": "preview",
                  "executed_at": "2026-06-03T20:12:15+09:00",
                  "best_theme": "반도체",
                  "theme_count": 9,
                  "global_ranked_leaders": [
                    {
                      "global_rank": 1,
                      "theme": "반도체",
                      "theme_key": "semiconductor",
                      "action": "매수",
                      "action_code": "BUY",
                      "confidence": 80,
                      "adjusted_leader_score": 65,
                      "leader": {
                        "candidate": {"stock_name": "LG이노텍", "stock_code": "011070"},
                        "analyst": {"summary": "AI 부품 테마 기대감이 높습니다."},
                        "quant": {"total_score": 100},
                        "chartist": {"signal": "중립"},
                        "final_decision": {
                          "summary": "산업과 실적 모멘텀을 근거로 분할 매수합니다.",
                          "risk_level": "보통",
                          "key_catalysts": ["AI 데이터센터 부품 수요"]
                        }
                      }
                    }
                  ]
                }
                """);

        HistoricalTradingSnapshotService service = serviceFor(tempDir);
        Map<String, Object> activity = service.aiActivity(5);

        assertThat(activity).containsEntry("status", "ok");
        assertThat(activity).containsEntry("bestTheme", "반도체");
        assertThat(activity.get("sourceReport")).isEqualTo("multi_theme_leader_trading_preview_20260603-201215.json");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> leaders = (List<Map<String, Object>>) activity.get("leaders");
        assertThat(leaders).hasSize(1);
        assertThat(leaders.get(0))
                .containsEntry("stockName", "LG이노텍")
                .containsEntry("stockCode", "011070")
                .containsEntry("action", "매수")
                .containsEntry("confidence", 80);
    }

    @Test
    void ordersNormalizeJsonlRowsForDashboard() throws Exception {
        Path day = Files.createDirectories(tempDir.resolve("orders").resolve("2026-06-08"));
        Files.writeString(day.resolve("orders.jsonl"), """
                {"timestamp":"2026-06-08T11:18:38+09:00","stock_name":"클로봇","stock_code":"466100","action":"SELL","quantity":5,"price":38800,"status":"submitted","kis_order_no":"0000016094"}
                """);

        HistoricalTradingSnapshotService service = serviceFor(tempDir);
        Map<String, Object> response = service.orders(null, 20);

        assertThat(response).containsEntry("status", "ok");
        assertThat(response).containsEntry("count", 1);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> orders = (List<Map<String, Object>>) response.get("orders");
        assertThat(orders).hasSize(1);
        assertThat(orders.get(0))
                .containsEntry("id", "0000016094")
                .containsEntry("stockName", "클로봇")
                .containsEntry("stockCode", "466100")
                .containsEntry("side", "sell")
                .containsEntry("quantity", 5)
                .containsEntry("price", 38800)
                .containsEntry("status", "submitted");
    }

    @Test
    void ordersPreferTradeSignalsStoredInDatabase() {
        TradeSignal signal = new TradeSignal();
        ReflectionTestUtils.setField(signal, "id", "sig-1");
        signal.setUserId("demo");
        signal.setSource("historical_runtime_report");
        signal.setStrategyProfile("short");
        signal.setThemeKey("battery");
        signal.setThemeName("2차전지");
        signal.setStockCode("413390");
        signal.setStockName("엠오티");
        signal.setAction("BUY");
        signal.setLeaderScore(65);
        signal.setConfidence(80);
        signal.setRiskLevel("보통");
        signal.setSignalPrice(7000L);
        signal.setReason("실제 리포트 기반 매수 신호");
        signal.setStatus("EXECUTED");
        signal.setExecutedAt(OffsetDateTime.parse("2026-06-03T20:12:15+09:00"));

        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId("sig-1");
        execution.setUserId("demo");
        execution.setStatus("EXECUTED");
        execution.setQuantity(10);
        execution.setOrderPrice(7000L);
        execution.setCurrentPrice(7350L);
        execution.setPriceDriftPct(0.0);

        TradeSignalRepository signalRepository = mock(TradeSignalRepository.class);
        TradeSignalExecutionRepository executionRepository = mock(TradeSignalExecutionRepository.class);
        when(signalRepository.findTop100ByOrderByCreatedAtDesc()).thenReturn(List.of(signal));
        when(executionRepository.findBySignalId(signal.getId())).thenReturn(List.of(execution));

        HistoricalTradingSnapshotService service = new HistoricalTradingSnapshotService(
                new HqaProperties(),
                new ObjectMapper(),
                signalRepository,
                executionRepository
        );

        Map<String, Object> response = service.orders(null, 20);

        assertThat(response).containsEntry("source", "database_trade_signals");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> orders = (List<Map<String, Object>>) response.get("orders");
        assertThat(orders).hasSize(1);
        assertThat(orders.get(0))
                .containsEntry("stockName", "엠오티")
                .containsEntry("stockCode", "413390")
                .containsEntry("side", "buy")
                .containsEntry("quantity", 10)
                .containsEntry("price", 7000L)
                .containsEntry("returnPct", 5.0);

        Map<String, Object> activity = service.aiActivity(5);
        assertThat(activity).containsEntry("source", "database_trade_signals");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> leaders = (List<Map<String, Object>>) activity.get("leaders");
        assertThat(leaders.get(0))
                .containsEntry("stockName", "엠오티")
                .containsEntry("returnPct", 5.0);
    }

    @Test
    void ordersOnlyUseTradeSignalsForRequestedUser() {
        TradeSignal mine = tradeSignal("sig-mine", "user-1", "005930", "삼성전자");
        TradeSignal other = tradeSignal("sig-other", "user-2", "000660", "SK하이닉스");

        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId("sig-mine");
        execution.setUserId("user-1");
        execution.setStatus("EXECUTED");
        execution.setQuantity(3);
        execution.setOrderPrice(70000L);
        execution.setCurrentPrice(71000L);

        TradeSignalRepository signalRepository = mock(TradeSignalRepository.class);
        TradeSignalExecutionRepository executionRepository = mock(TradeSignalExecutionRepository.class);
        when(signalRepository.findTop100ByUserIdOrderByCreatedAtDesc("user-1")).thenReturn(List.of(mine));
        when(signalRepository.findTop100ByUserIdOrderByCreatedAtDesc("user-2")).thenReturn(List.of(other));
        when(executionRepository.findBySignalId("sig-mine")).thenReturn(List.of(execution));

        HistoricalTradingSnapshotService service = new HistoricalTradingSnapshotService(
                new HqaProperties(),
                new ObjectMapper(),
                signalRepository,
                executionRepository
        );

        Map<String, Object> response = service.orders("user-1", null, 20);

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> orders = (List<Map<String, Object>>) response.get("orders");
        assertThat(orders).hasSize(1);
        assertThat(orders.get(0))
                .containsEntry("stockCode", "005930")
                .containsEntry("stockName", "삼성전자");
    }

    @Test
    void balanceOnlyUsesPositionsForRequestedUser() {
        TradeSignal mine = tradeSignal("sig-mine", "user-1", "005930", "삼성전자");
        TradeSignal other = tradeSignal("sig-other", "user-2", "000660", "SK하이닉스");

        TradeSignalExecution mineExecution = new TradeSignalExecution();
        mineExecution.setSignalId("sig-mine");
        mineExecution.setUserId("user-1");
        mineExecution.setStatus("EXECUTED");
        mineExecution.setQuantity(2);
        mineExecution.setOrderPrice(70000L);
        mineExecution.setCurrentPrice(71000L);

        TradeSignalExecution otherExecution = new TradeSignalExecution();
        otherExecution.setSignalId("sig-other");
        otherExecution.setUserId("user-2");
        otherExecution.setStatus("EXECUTED");
        otherExecution.setQuantity(9);
        otherExecution.setOrderPrice(120000L);
        otherExecution.setCurrentPrice(121000L);

        TradeSignalRepository signalRepository = mock(TradeSignalRepository.class);
        TradeSignalExecutionRepository executionRepository = mock(TradeSignalExecutionRepository.class);
        when(signalRepository.findTop100ByUserIdOrderByCreatedAtDesc("user-1")).thenReturn(List.of(mine));
        when(signalRepository.findTop100ByUserIdOrderByCreatedAtDesc("user-2")).thenReturn(List.of(other));
        when(executionRepository.findBySignalId("sig-mine")).thenReturn(List.of(mineExecution));
        when(executionRepository.findBySignalId("sig-other")).thenReturn(List.of(otherExecution));

        HistoricalTradingSnapshotService service = new HistoricalTradingSnapshotService(
                new HqaProperties(),
                new ObjectMapper(),
                signalRepository,
                executionRepository
        );

        Map<String, Object> response = service.balance("user-1");

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> holdings = (List<Map<String, Object>>) response.get("holdings");
        assertThat(holdings).hasSize(1);
        assertThat(holdings.get(0))
                .containsEntry("stockCode", "005930")
                .containsEntry("stockName", "삼성전자")
                .containsEntry("quantity", 2);
    }

    private TradeSignal tradeSignal(String id, String userId, String stockCode, String stockName) {
        TradeSignal signal = new TradeSignal();
        ReflectionTestUtils.setField(signal, "id", id);
        signal.setUserId(userId);
        signal.setSource("test");
        signal.setStrategyProfile("short");
        signal.setThemeKey("semiconductor");
        signal.setThemeName("반도체");
        signal.setStockCode(stockCode);
        signal.setStockName(stockName);
        signal.setAction("BUY");
        signal.setLeaderScore(80);
        signal.setConfidence(75);
        signal.setRiskLevel("보통");
        signal.setSignalPrice(70000L);
        signal.setReason("사용자별 신호");
        signal.setStatus("EXECUTED");
        signal.setExecutedAt(OffsetDateTime.parse("2026-06-03T20:12:15+09:00"));
        return signal;
    }

    private HistoricalTradingSnapshotService serviceFor(Path dataDir) {
        HqaProperties properties = new HqaProperties();
        properties.setHistoricalDataDir(dataDir.toString());
        return new HistoricalTradingSnapshotService(properties, new ObjectMapper());
    }
}
