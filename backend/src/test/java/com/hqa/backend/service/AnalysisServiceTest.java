package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

import com.hqa.backend.dto.AnalysisMode;
import com.hqa.backend.dto.AnalysisRequest;
import com.hqa.backend.dto.BulkAnalysisResponse;
import com.hqa.backend.exception.ApiException;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;

class AnalysisServiceTest {

    private final AiServerClient aiServerClient = mock(AiServerClient.class);
    private final StockCatalogService stockCatalogService = mock(StockCatalogService.class);
    private final AnalysisService service = new AnalysisService(aiServerClient, stockCatalogService);

    @Test
    void submitRejectsLegacySingleStockAnalysisFlow() {
        AnalysisRequest request = request("삼성전자", "005930", AnalysisMode.full);

        assertThatThrownBy(() -> service.submit(request))
                .isInstanceOf(ApiException.class)
                .hasMessageContaining("기존 단일 종목 분석 API는 제거되었습니다")
                .extracting("status")
                .isEqualTo(410);
    }

    @Test
    void submitBulkFromItemsReturnsFailuresWithoutCallingLegacyAiAnalyze() {
        BulkAnalysisResponse response = service.submitBulkFromItems(
                List.of(
                        Map.of("stockName", "삼성전자", "stockCode", "005930"),
                        Map.of("stockName", "SK하이닉스", "stockCode", "000660")
                ),
                AnalysisMode.quick,
                0
        );

        assertThat(response.total()).isEqualTo(2);
        assertThat(response.submitted()).isZero();
        assertThat(response.failed()).isEqualTo(2);
        assertThat(response.failures())
                .extracting(BulkAnalysisResponse.BulkAnalysisFailure::reason)
                .containsOnly("legacy analysis flow removed");
    }

    @Test
    void collectAgentResultEventsEmitsOnlyNewAgentScores() {
        Set<String> emitted = new HashSet<>(Set.of("quant"));
        Map<String, Object> aiData = Map.of(
                "scores", Map.of(
                        "quant", Map.of("total_score", 80, "grade", "A", "opinion", "재무 우수"),
                        "chartist", Map.of("total_score", 65, "signal", "매수")
                )
        );

        List<Map<String, Object>> events = service.collectAgentResultEvents(aiData, emitted);

        assertThat(events).hasSize(1);
        assertThat(events.get(0))
                .containsEntry("agent", "chartist")
                .containsEntry("status", "completed")
                .containsEntry("total_score", 65.0)
                .containsEntry("grade", "매수");
        assertThat(String.valueOf(events.get(0).get("message"))).contains("Chartist");
        assertThat(emitted).containsExactlyInAnyOrder("quant", "chartist");
    }

    @Test
    void progressEventCanBecomeAgentResultWhenAgentCompletes() {
        Map<String, Object> progress = Map.of(
                "agent", "quant",
                "status", "completed",
                "message", "재무: F",
                "progress", 1.0,
                "timestamp", "2026-06-13T02:10:00"
        );

        Map<String, Object> event = service.agentResultFromProgress(progress);

        assertThat(event)
                .containsEntry("agent", "quant")
                .containsEntry("label", "Quant")
                .containsEntry("status", "completed")
                .containsEntry("message", "Quant 완료: 재무: F");
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
