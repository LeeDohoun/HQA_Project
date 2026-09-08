package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.hqa.backend.dto.*;
import com.hqa.backend.entity.*;
import com.hqa.backend.repository.AnalysisRecordRepository;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

class AnalysisServiceTest {
    final AiServerClient ai = mock(AiServerClient.class);
    final AnalysisRecordRepository records = mock(AnalysisRecordRepository.class);
    final WatchlistService watchlist = mock(WatchlistService.class);
    final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());
    final AnalysisService service = new AnalysisService(ai, records, watchlist, mapper);
    final User user = new User();
    final String id = "083f6f45-19fa-4d2b-8f12-e12975649227";
    AnalysisRecord saved;

    @BeforeEach
    void setup() {
        ReflectionTestUtils.setField(user, "id", "u1");
        when(ai.submitStockPreview("005930")).thenReturn(Map.of("task_id", id, "status", "queued"));
        when(records.save(any())).thenAnswer(inv -> {
            saved = inv.getArgument(0);
            when(records.findByTaskIdAndUser_Id(id, "u1")).thenReturn(Optional.of(saved));
            return saved;
        });
    }

    @Test
    void submitUsesCurrentPreviewAndPersistsOwner() {
        assertThat(service.submit(request(), user).taskId()).isEqualTo(id);
        assertThat(saved.getUser()).isSameAs(user);
        assertThat(saved.getMaxRetries()).isZero();
        verify(ai).submitStockPreview("005930");
    }

    @Test
    void completedResultsSurviveServiceRestartWithoutCallingTheAiServerAgain() {
        service.submit(request(), user);
        Map<String, Object> specialists = new LinkedHashMap<>();
        for (String role : List.of("analyst", "quant", "chartist")) {
            specialists.put(role, Map.of("role", role, "stock_code", "005930", "score", 75.0, "thesis", "근거",
                    "citations", List.of(Map.of("source_id", "doc:1", "claim", "공시"))));
        }
        when(ai.getRuntimeTask(id)).thenReturn(Map.of("task_id", id, "status", "completed", "result", Map.of(
                "stock_code", "005930", "stock_name", "삼성전자", "status", "completed",
                "specialists", specialists, "data_gaps", List.of(), "errors", Map.of()),
                "completed_at", saved.getCreatedAt().plusSeconds(2).toString()));
        var result = service.getResult(id, user);
        assertThat(result.status()).isEqualTo(AnalysisStatus.completed);
        assertThat(result.scores()).hasSize(3).allSatisfy(s -> assertThat(s.maxScore()).isEqualTo(100));
        assertThat(result.finalDecision()).isNull();
        assertThat(result.durationSeconds()).isEqualTo(2.0);
        assertThat(saved.getResultJson()).isNotBlank();
        var restarted = new AnalysisService(ai, records, watchlist, mapper);
        assertThat(restarted.getResult(id, user)).usingRecursiveComparison()
                .withComparatorForType(Comparator.comparing(java.time.OffsetDateTime::toInstant), java.time.OffsetDateTime.class)
                .isEqualTo(result);
        when(records.findByUser_IdOrderByCreatedAtDesc(eq("u1"), any()))
                .thenReturn(new org.springframework.data.domain.PageImpl<>(List.of(saved)));
        assertThat(restarted.getHistory(1, 20, user).items().get(0).totalScore()).isEqualTo(75.0);
        verify(ai, times(1)).getRuntimeTask(id);
    }

    @Test
    void otherUsersCannotReadOrPollATask() {
        service.submit(request(), user);
        var other = new User(); ReflectionTestUtils.setField(other, "id", "u2");
        assertThatThrownBy(() -> service.getResult(id, other)).hasMessageContaining("찾지 못했습니다");
        assertThatThrownBy(() -> service.getProgress(id, other)).hasMessageContaining("찾지 못했습니다");
        verify(ai, never()).getRuntimeTask(anyString());
    }

    @Test
    void failuresArePersistedAsFailuresWithoutInventingScores() {
        service.submit(request(), user);
        when(ai.getRuntimeTask(id)).thenReturn(Map.of("task_id", id, "status", "failed", "error", "missing_price_history",
                "failed_at", saved.getCreatedAt().plusSeconds(1).toString()));
        var result = service.getResult(id, user);
        assertThat(result.status()).isEqualTo(AnalysisStatus.failed);
        assertThat(result.errors()).containsEntry("runtime", "missing_price_history");
        assertThat(result.scores()).isEmpty();
    }

    @Test
    void staleQueuedResponseCannotRegressRunningState() {
        service.submit(request(), user);
        saved.setStatus("running");
        when(ai.getRuntimeTask(id)).thenReturn(Map.of("task_id", id, "status", "queued"));
        assertThat(service.getResult(id, user).status()).isEqualTo(AnalysisStatus.running);
        verify(records, times(1)).save(any()); // Submission only; polling never merges stale entities.
    }

    @Test
    void missingRuntimeTaskIsSavedAsAnExplicitFailure() {
        service.submit(request(), user);
        when(ai.getRuntimeTask(id)).thenThrow(new com.hqa.backend.exception.ApiException(
                ErrorCode.ANALYSIS_NOT_FOUND, 404, "expired", null));
        assertThat(service.getResult(id, user).errors().get("runtime")).contains("소실");
        assertThat(service.getResult(id, user).status()).isEqualTo(AnalysisStatus.failed);
        verify(ai, times(1)).getRuntimeTask(id);
    }

    @Test
    void pendingJobsDoNotMakeStoredHistoryDependOnAiAvailability() {
        service.submit(request(), user);
        when(records.findByUser_IdOrderByCreatedAtDesc(eq("u1"), any()))
                .thenReturn(new org.springframework.data.domain.PageImpl<>(List.of(saved)));
        assertThat(service.getHistory(1, 20, user).items().get(0).status()).isEqualTo(AnalysisStatus.pending);
        verify(ai, never()).getRuntimeTask(anyString());
    }

    @Test
    void rejectsUnsupportedModesAndInvalidCodesBeforePaidWork() {
        var request = request(); request.setMode(AnalysisMode.quick);
        assertThatThrownBy(() -> service.submit(request, user)).hasMessageContaining("full");
        request.setMode(AnalysisMode.full); request.setStockCode(null);
        assertThatThrownBy(() -> service.submit(request, user)).hasMessageContaining("6자리");
        verifyNoInteractions(ai);
    }

    private AnalysisRequest request() {
        var request = new AnalysisRequest(); request.setStockName("삼성전자"); request.setStockCode("005930");
        return request;
    }
}
