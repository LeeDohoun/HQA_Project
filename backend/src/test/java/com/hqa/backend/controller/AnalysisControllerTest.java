package com.hqa.backend.controller;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.hqa.backend.dto.AnalysisMode;
import com.hqa.backend.dto.BulkAnalysisRequest;
import com.hqa.backend.service.AnalysisService;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class AnalysisControllerTest {

    @Test
    void bulkItemsPreserveRequestedAnalysisMode() {
        AnalysisService analysisService = mock(AnalysisService.class);
        AnalysisController controller = new AnalysisController(analysisService);
        BulkAnalysisRequest.BulkAnalysisItem item = new BulkAnalysisRequest.BulkAnalysisItem();
        item.setStockName("삼성전자");
        item.setStockCode("005930");
        BulkAnalysisRequest request = new BulkAnalysisRequest();
        request.setItems(List.of(item));

        controller.createBulk(AnalysisMode.full, 1, request);

        verify(analysisService).submitBulkFromItems(
                org.mockito.ArgumentMatchers.anyList(),
                org.mockito.ArgumentMatchers.eq(AnalysisMode.full),
                org.mockito.ArgumentMatchers.eq(1)
        );
    }

    @Test
    void progressDelegatesToAnalysisService() {
        AnalysisService analysisService = mock(AnalysisService.class);
        when(analysisService.getProgress("task-1")).thenReturn(Map.of("task_id", "task-1", "events", List.of()));
        AnalysisController controller = new AnalysisController(analysisService);

        controller.getProgress("task-1");

        verify(analysisService).getProgress("task-1");
    }
}
