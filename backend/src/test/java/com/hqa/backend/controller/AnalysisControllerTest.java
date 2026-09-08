package com.hqa.backend.controller;

import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.hqa.backend.dto.*;
import com.hqa.backend.exception.*;
import com.hqa.backend.service.*;
import com.hqa.backend.entity.User;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class AnalysisControllerTest {
    @Test
    void anonymousChatAndSuggestionsNeverReachPaidServices() throws Exception {
        var auth = mock(AuthService.class);
        var analysis = mock(AnalysisService.class);
        var ai = mock(AiServerClient.class);
        when(auth.requireUser(any())).thenThrow(new ApiException(ErrorCode.UNAUTHORIZED, 401, "Login required", null));
        var mvc = MockMvcBuilders.standaloneSetup(new ChatController(ai, auth), new AnalysisController(analysis, auth))
                .setControllerAdvice(new GlobalExceptionHandler()).build();
        mvc.perform(post("/api/v1/chat").contentType("application/json").content("{\"message\":\"hello\"}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/api/v1/analysis/suggest").contentType("application/json").content("{\"query\":\"삼성전자\"}"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(ai, analysis);
    }

    @Test
    void bulkAndProgressCarryTheAuthenticatedOwner() {
        var service = mock(AnalysisService.class);
        var auth = mock(AuthService.class);
        var session = new MockHttpSession(); var user = new User();
        when(auth.requireUser(session)).thenReturn(user);
        var controller = new AnalysisController(service, auth);
        var item = new BulkAnalysisRequest.BulkAnalysisItem(); item.setStockCode("005930"); item.setStockName("삼성전자");
        var request = new BulkAnalysisRequest(); request.setItems(List.of(item));
        controller.createBulk(AnalysisMode.full, 0, request, session);
        controller.getProgress("task-1", session);
        verify(service).submitBulkFromItems(anyList(), eq(AnalysisMode.full), eq(0), same(user));
        verify(service).getProgress("task-1", user);
    }

    @Test
    void explicitEmptySelectionDoesNotAnalyzeTheWholeWatchlist() {
        var service = mock(AnalysisService.class);
        var auth = mock(AuthService.class);
        var session = new MockHttpSession(); var user = new User();
        when(auth.requireUser(session)).thenReturn(user);
        new AnalysisController(service, auth).createBulk(AnalysisMode.full, 0, new BulkAnalysisRequest(), session);
        verify(service).submitBulkFromItems(eq(List.of()), eq(AnalysisMode.full), eq(0), same(user));
        verify(service, never()).submitBulkFromWatchlist(any(), anyInt(), any());
    }

    @Test
    void nullBulkItemIsRejectedBeforeAnyPaidWork() throws Exception {
        var service = mock(AnalysisService.class);
        var auth = mock(AuthService.class);
        var mvc = MockMvcBuilders.standaloneSetup(new AnalysisController(service, auth))
                .setControllerAdvice(new GlobalExceptionHandler()).build();
        mvc.perform(post("/api/v1/analysis/bulk").contentType("application/json").content("{\"items\":[null]}"))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(service);
    }
}
