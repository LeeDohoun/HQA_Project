package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.hqa.backend.dto.AiAnalyzeRequest;
import com.hqa.backend.dto.AnalysisHistoryResponse;
import com.hqa.backend.dto.AnalysisMode;
import com.hqa.backend.dto.AnalysisRequest;
import com.hqa.backend.dto.AnalysisResultResponse;
import com.hqa.backend.dto.AnalysisTaskResponse;
import com.hqa.backend.dto.BulkAnalysisResponse;
import com.hqa.backend.dto.StockInfo;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

class AnalysisServiceTest {

    private final AiServerClient aiServerClient = mock(AiServerClient.class);
    private final StockCatalogService stockCatalogService = mock(StockCatalogService.class);
    private final AnalysisService service = new AnalysisService(aiServerClient, stockCatalogService);

    @Test
    void submitBulkFromItemsSubmitsEachUiWatchlistItemAsQuickAnalysis() {
        BulkAnalysisResponse response = service.submitBulkFromItems(
                List.of(
                        Map.of("stockName", "삼성전자", "stockCode", "005930"),
                        Map.of("stockName", "SK하이닉스", "stockCode", "000660")
                ),
                AnalysisMode.quick,
                0
        );

        ArgumentCaptor<AiAnalyzeRequest> captor = ArgumentCaptor.forClass(AiAnalyzeRequest.class);
        verify(aiServerClient, org.mockito.Mockito.times(2)).submitAnalysis(captor.capture());

        assertThat(response.total()).isEqualTo(2);
        assertThat(response.submitted()).isEqualTo(2);
        assertThat(response.failed()).isZero();
        assertThat(captor.getAllValues())
                .extracting(AiAnalyzeRequest::stockCode, AiAnalyzeRequest::mode)
                .containsExactly(
                        org.assertj.core.api.Assertions.tuple("005930", "quick"),
                        org.assertj.core.api.Assertions.tuple("000660", "quick")
                );
    }

    @Test
    void getResultMapsRiskManagerAndQuickDecisionScores() {
        when(stockCatalogService.getStockInfo("005930")).thenReturn(new StockInfo("삼성전자", "005930"));
        AnalysisTaskResponse task = service.submit(request("삼성전자", "005930", AnalysisMode.quick));
        when(aiServerClient.getAnalysis(task.taskId())).thenReturn(Map.of(
                "status", "completed",
                "completed_at", "2026-06-02T01:02:03Z",
                "scores", Map.of(
                        "quant", Map.of("total_score", 80, "grade", "A", "opinion", "재무 우수"),
                        "chartist", Map.of("total_score", 65, "signal", "매수"),
                        "quick_decision", Map.of("total_score", 70, "grade", "매수", "opinion", "빠른 판단")
                ),
                "final_decision", Map.of("total_score", 70, "action", "매수", "summary", "빠른 판단")
        ));

        AnalysisResultResponse result = service.getResult(task.taskId());

        assertThat(result.scores())
                .extracting("agent")
                .containsExactly("quant", "chartist", "quick_decision");
        assertThat(result.finalDecision()).containsEntry("action", "매수");
        assertThat(result.completedAt()).isNotNull();
    }

    @Test
    void getHistoryIncludesLatestScoreActionAndCompletedAt() {
        when(stockCatalogService.getStockInfo("005930")).thenReturn(new StockInfo("삼성전자", "005930"));
        AnalysisTaskResponse task = service.submit(request("삼성전자", "005930", AnalysisMode.full));
        when(aiServerClient.getAnalysis(task.taskId())).thenReturn(Map.of(
                "status", "completed",
                "completed_at", "2026-06-02T01:02:03Z",
                "scores", Map.of(
                        "analyst", Map.of("total_score", 60, "hegemony_grade", "A", "final_opinion", "우수"),
                        "quant", Map.of("total_score", 80, "grade", "A", "opinion", "재무 우수"),
                        "chartist", Map.of("total_score", 70, "signal", "매수"),
                        "risk_manager", Map.of("total_score", 82, "grade", "매수", "opinion", "최종 매수")
                ),
                "final_decision", Map.of("total_score", 82, "action", "매수", "summary", "최종 매수")
        ));
        service.getResult(task.taskId());

        AnalysisHistoryResponse history = service.getHistory(1, 10);

        assertThat(history.items()).hasSize(1);
        assertThat(history.items().get(0).totalScore()).isEqualTo(82.0);
        assertThat(history.items().get(0).action()).isEqualTo("매수");
        assertThat(history.items().get(0).completedAt()).isNotNull();
    }

    @Test
    void getResultTreatsAiCompletedAtWithoutOffsetAsLocalTime() {
        when(stockCatalogService.getStockInfo("005930")).thenReturn(new StockInfo("삼성전자", "005930"));
        AnalysisTaskResponse task = service.submit(request("삼성전자", "005930", AnalysisMode.quick));
        String completedAt = LocalDateTime.now().plusSeconds(5).toString();
        when(aiServerClient.getAnalysis(task.taskId())).thenReturn(Map.of(
                "status", "completed",
                "completed_at", completedAt,
                "scores", Map.of(
                        "quant", Map.of("total_score", 80, "grade", "A", "opinion", "재무 우수"),
                        "chartist", Map.of("total_score", 65, "signal", "매수"),
                        "quick_decision", Map.of("total_score", 70, "grade", "매수", "opinion", "빠른 판단")
                ),
                "final_decision", Map.of("total_score", 70, "action", "매수", "summary", "빠른 판단")
        ));

        AnalysisResultResponse result = service.getResult(task.taskId());

        assertThat(result.durationSeconds()).isBetween(0.0, 30.0);
    }

    private AnalysisRequest request(String name, String code, AnalysisMode mode) {
        AnalysisRequest request = new AnalysisRequest();
        request.setStockName(name);
        request.setStockCode(code);
        request.setMode(mode);
        request.setMaxRetries(mode == AnalysisMode.full ? 1 : 0);
        return request;
    }
}
