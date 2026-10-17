package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.dto.StrategyStatusRequest;
import com.hqa.backend.entity.StrategyStatusSnapshot;
import com.hqa.backend.repository.StrategyStatusSnapshotRepository;
import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class StrategyStatusServiceTest {
    private final StrategyTestFixture f = new StrategyTestFixture();
    private final StrategyStatusSnapshotRepository repository = mock(StrategyStatusSnapshotRepository.class);
    private final Map<String, StrategyStatusSnapshot> rows = new HashMap<>();
    private final StrategyStatusService service = new StrategyStatusService(repository, f.batches, f.orders, f.risk,
            f.sessions, f.mapper);

    StrategyStatusServiceTest() {
        when(repository.findById(anyString())).thenAnswer(inv -> Optional.ofNullable(rows.get(inv.<String>getArgument(0))));
        when(repository.save(any())).thenAnswer(inv -> {
            StrategyStatusSnapshot row = inv.getArgument(0);
            rows.put(row.getStrategyId(), row);
            return row;
        });
        when(repository.findAllByOrderByStrategyIdAsc()).thenAnswer(inv -> List.copyOf(rows.values()));
    }

    private static StrategyStatusRequest status(String at, int sessions) {
        return new StrategyStatusRequest("hf001_f_both", OffsetDateTime.parse(at),
                Map.of("strategyId", "hf001_f_both", "shadow", Map.of("sessions", sessions)));
    }

    @Test
    void onlyTheLatestStatusPerStrategyIsKept() {
        assertThat(service.publish(status("2026-10-30T17:10:00+09:00", 1))).containsEntry("stored", true);
        assertThat(service.publish(status("2026-10-30T17:00:00+09:00", 0))).containsEntry("stored", false);
        assertThat(service.publish(status("2026-10-31T17:10:00+09:00", 2))).containsEntry("stored", true);
        assertThat(rows).hasSize(1);
        assertThat(rows.get("hf001_f_both").getPayload()).contains("\"sessions\":2");
        assertThatThrownBy(() -> service.publish(new StrategyStatusRequest("av001_av_all", OffsetDateTime.now(),
                Map.of("strategyId", "hf001_f_both")))).hasMessage("STRATEGY_MISMATCH");
    }

    @Test
    void overviewCombinesStatusLatestBatchAndHaltsWithoutBrokerCalls() {
        service.publish(status("2026-10-30T17:10:00+09:00", 1));
        f.queued(basket());
        Map<String, Object> overview = service.overview();
        @SuppressWarnings("unchecked")
        Map<String, Object> strategy = ((List<Map<String, Object>>) overview.get("strategies")).get(0);
        assertThat(strategy).containsEntry("strategyId", "hf001_f_both").containsEntry("risk", null);
        assertThat(((Map<?, ?>) strategy.get("payload")).containsKey("shadow")).isTrue();
        @SuppressWarnings("unchecked")
        Map<String, Object> batch = (Map<String, Object>) strategy.get("latestBatch");
        assertThat(batch).containsEntry("phase", "OPEN").containsEntry("sessionDate", "2026-11-02").containsEntry("status", "QUEUED")
                .containsEntry("orderCount", 2).containsEntry("filledCount", 0L).containsEntry("rejectedCount", 0L)
                .containsEntry("avgSlippagePct", null);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> halts = (List<Map<String, Object>>) overview.get("halts");
        assertThat(halts).hasSize(1);
        assertThat(halts.get(0)).containsEntry("scope", "GLOBAL").containsEntry("halted", false);
        verify(f.snapshots, times(1)).snapshot("u1"); // only the batch acceptance read the account
        verifyNoInteractions(f.kis);
    }
}
