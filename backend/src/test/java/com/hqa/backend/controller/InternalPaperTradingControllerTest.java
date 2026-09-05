package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.PaperAccountSnapshotService;
import com.hqa.backend.service.TradeSignalService;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class InternalPaperTradingControllerTest {
    @Test
    void snapshotsRejectMissingConfigAndWrongTokenBeforeAccountAccess() {
        HqaProperties properties = new HqaProperties();
        PaperAccountSnapshotService service = mock(PaperAccountSnapshotService.class);
        InternalAccountSnapshotController controller = new InternalAccountSnapshotController(service, properties);
        var request = new InternalAccountSnapshotController.Request(List.of("u1"));
        assertThatThrownBy(() -> controller.snapshots(request, null)).isInstanceOf(ApiException.class);
        properties.setInternalToken("test-token");
        assertThatThrownBy(() -> controller.snapshots(request, "wrong")).isInstanceOf(ApiException.class);
        verifyNoInteractions(service);
        when(service.snapshot("u1")).thenReturn(Map.of("userId", "u1", "success", false, "error", "DAILY_BASELINE_UNAVAILABLE"));
        assertThat((List<?>) controller.snapshots(request, "test-token").get("snapshots")).hasSize(1);
    }

    @Test
    void triggerReturnsExplicitBlockedOutcomeAndActivePaginationUnchanged() {
        HqaProperties properties = new HqaProperties();
        properties.setInternalToken("test-token");
        TradeSignalService service = mock(TradeSignalService.class);
        InternalTradeSignalController controller = new InternalTradeSignalController(service, properties);
        Map<String, Object> blocked = Map.of("signalId", "s1", "status", "OPEN", "accepted", false,
                "deduplicated", false, "rejectReason", "STALE_PLAN_VERSION");
        when(service.triggerResponse(eq("s1"), anyMap())).thenReturn(blocked);
        assertThat(controller.trigger("s1", Map.of("planVersion", 1), "test-token")).isEqualTo(blocked);
        when(service.activeSignalsForMonitor(1, 200)).thenReturn(Map.of("signals", List.of(), "hasMore", false));
        assertThat(controller.active("test-token", 1, 200)).containsEntry("hasMore", false);
    }
}
