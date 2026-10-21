package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.dto.StrategyStatusRequest;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.exception.GlobalExceptionHandler;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.StrategyAccountService;
import com.hqa.backend.service.StrategyBatchService;
import com.hqa.backend.service.StrategyOrderResolver;
import com.hqa.backend.service.StrategyRiskGuard;
import com.hqa.backend.service.StrategyStatusService;
import com.hqa.backend.service.StrategyTerminationService;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class InternalStrategyControllerTest {
    /** Configured like the production Spring mapper (SNAKE_CASE default naming). */
    private final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule())
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    private final HqaProperties properties = new HqaProperties();
    private final StrategyBatchService batches = mock(StrategyBatchService.class);
    private final StrategyAccountService accounts = mock(StrategyAccountService.class);
    private final StrategyRiskGuard risk = mock(StrategyRiskGuard.class);
    private final StrategyStatusService statuses = mock(StrategyStatusService.class);
    private final StrategyOrderResolver resolver = mock(StrategyOrderResolver.class);
    private final StrategyTerminationService termination = mock(StrategyTerminationService.class);
    private final InternalStrategyController controller = new InternalStrategyController(batches, accounts, risk, statuses,
            resolver, termination, properties);

    InternalStrategyControllerTest() {
        properties.setInternalToken("contract-test");
    }

    private MockMvc mvc() {
        return MockMvcBuilders.standaloneSetup(controller).setControllerAdvice(new GlobalExceptionHandler())
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();
    }

    @Test
    void everyEndpointRequiresTheInternalToken() {
        assertThatThrownBy(() -> controller.createBatch(null, "wrong")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.batch("x", null)).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.publishStatus(null, "")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.accounts("wrong")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.bind(null, "wrong")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.unbind("u1", "wrong")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.halt(null, "wrong")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.risk("u1", "wrong")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.unknownOrders("wrong")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> controller.resolveOrder("x", null, "wrong")).isInstanceOf(ApiException.class);
        properties.setInternalToken("");
        assertThatThrownBy(() -> controller.accounts("")).isInstanceOf(ApiException.class);
        verifyNoInteractions(batches, accounts, risk, statuses, resolver);
    }

    @Test
    void batchRequestsUseCamelCaseOverHttpWithTheSnakeCaseMapper() throws Exception {
        when(batches.create(any())).thenReturn(Map.of("batchId", "b1", "status", "QUEUED", "deduplicated", false,
                "reasons", List.of(), "orderCount", 1, "rejectedOrders", List.of()));
        mvc().perform(post("/api/v1/internal/strategies/batches").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("""
                {"strategyId":"hf001_f_both","userId":"u1","decisionHash":"%s","decisionDate":"2026-10-30",
                 "sessionDate":"2026-11-02","phase":"OPEN","idempotencyKey":"%s","dryRun":false,"expectedNavKrw":100000000,
                 "orders":[{"seq":1,"stockCode":"005930","side":"SELL","quantity":10,"refPrice":70000,"adv20Krw":12000000000.0}]}
                """.formatted("a".repeat(64), "b".repeat(64))))
                .andExpect(status().isOk()).andExpect(jsonPath("$.batchId").value("b1"))
                .andExpect(jsonPath("$.orderCount").value(1)).andExpect(jsonPath("$.rejectedOrders").isArray());
        ArgumentCaptor<StrategyBatchRequest> captor = ArgumentCaptor.forClass(StrategyBatchRequest.class);
        verify(batches).create(captor.capture());
        StrategyBatchRequest request = captor.getValue();
        assertThat(request.strategyId()).isEqualTo("hf001_f_both");
        assertThat(request.sessionDate()).isEqualTo(LocalDate.parse("2026-11-02"));
        assertThat(request.dryRun()).isFalse();
        assertThat(request.expectedNavKrw()).isEqualTo(100_000_000L);
        assertThat(request.orders().get(0).stockCode()).isEqualTo("005930");
        assertThat(request.orders().get(0).refPrice()).isEqualTo(70_000L);
        assertThat(request.orders().get(0).adv20Krw()).isEqualTo(12_000_000_000.0);
    }

    @Test
    void statusAccountsHaltAndRiskRoutes() throws Exception {
        when(statuses.publish(any())).thenReturn(Map.of("stored", true));
        MockMvc mvc = mvc();
        mvc.perform(post("/api/v1/internal/strategies/status").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("""
                {"strategyId":"hf001_f_both","generatedAt":"2026-10-30T17:10:00+09:00",
                 "payload":{"strategyId":"hf001_f_both","shadow":{"navKrw":100000000}}}
                """)).andExpect(status().isOk()).andExpect(jsonPath("$.stored").value(true));
        ArgumentCaptor<StrategyStatusRequest> status = ArgumentCaptor.forClass(StrategyStatusRequest.class);
        verify(statuses).publish(status.capture());
        assertThat(status.getValue().payload()).containsKey("shadow");
        assertThat(status.getValue().generatedAt().toInstant()).isEqualTo(java.time.Instant.parse("2026-10-30T08:10:00Z"));

        when(accounts.bind("u1", "hf001_f_both", "observe", false)).thenReturn(Map.of("userId", "u1"));
        mvc.perform(post("/api/v1/internal/strategies/accounts").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("""
                {"userId":"u1","strategyId":"hf001_f_both","stage":"observe","enabled":false}
                """)).andExpect(status().isOk()).andExpect(jsonPath("$.userId").value("u1"));

        when(risk.setHalt("GLOBAL", true, "drill")).thenReturn(Map.of("scope", "GLOBAL", "halted", true));
        mvc.perform(post("/api/v1/internal/strategies/halt").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("{\"scope\":\"GLOBAL\",\"halted\":true,\"reason\":\"drill\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.halted").value(true));

        mvc.perform(get("/api/v1/internal/strategies/risk/nobody").header("X-HQA-Internal-Token", "contract-test"))
                .andExpect(status().isNotFound());
        verify(risk, never()).evaluate(anyString());
        mvc.perform(get("/api/v1/internal/strategies/batches/not-a-uuid").header("X-HQA-Internal-Token", "contract-test"))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/api/v1/internal/strategies/accounts")).andExpect(status().isUnauthorized());

        java.util.UUID orderId = java.util.UUID.randomUUID();
        when(resolver.resolve(orderId, "adopt", "0000012345", "앱 확인")).thenReturn(Map.of("status", "SUBMITTED"));
        mvc.perform(post("/api/v1/internal/strategies/orders/" + orderId + "/resolution").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("{\"action\":\"adopt\",\"brokerOrderId\":\"0000012345\",\"note\":\"앱 확인\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("SUBMITTED"));
        when(resolver.unknownOrders()).thenReturn(Map.of("orders", List.of()));
        mvc.perform(get("/api/v1/internal/strategies/orders/unknown").header("X-HQA-Internal-Token", "contract-test"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.orders").isArray());
    }

    @Test
    void publicOverviewRequiresALoggedInUser() {
        AuthService auth = mock(AuthService.class);
        MockHttpSession session = new MockHttpSession();
        when(auth.requireUser(session)).thenThrow(new ApiException(com.hqa.backend.dto.ErrorCode.UNAUTHORIZED, 401, "login", null));
        StrategyController publicController = new StrategyController(auth, statuses);
        assertThatThrownBy(() -> publicController.strategies(session)).isInstanceOf(ApiException.class);
        verifyNoInteractions(statuses);
    }

    @Test
    void navCloseUsesCamelCaseAndAConflictIs409() throws Exception {
        when(termination.recordOfficialClose("u1", LocalDate.parse("2026-11-02"), 101_000_000L, 40_000_000L, 61_000_000L,
                "krx_official", "abc")).thenReturn(Map.of("deduplicated", false, "terminated", false));
        MockMvc mvc = mvc();
        String body = """
                {"sessionDate":"2026-11-02","navKrw":101000000,"cashKrw":40000000,"holdingsValueKrw":61000000,
                 "source":"krx_official","inputHash":"abc"}
                """;
        mvc.perform(post("/api/v1/internal/strategies/accounts/u1/nav-close").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content(body))
                .andExpect(status().isOk()).andExpect(jsonPath("$.terminated").value(false));
        when(termination.recordOfficialClose(any(), any(), any(), any(), any(), any(), any()))
                .thenThrow(new IllegalStateException("NAV_CLOSE_CONFLICT"));
        mvc.perform(post("/api/v1/internal/strategies/accounts/u1/nav-close").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content(body))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.message").value("NAV_CLOSE_CONFLICT"));
        mvc.perform(post("/api/v1/internal/strategies/accounts/u1/nav-close").contentType("application/json").content(body))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void contBuyCostReserveRateIsReadFromCamelCase() throws Exception {
        when(batches.create(any())).thenReturn(Map.of("status", "VALIDATED"));
        mvc().perform(post("/api/v1/internal/strategies/batches").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("""
                {"strategyId":"pf001","userId":"u1","decisionHash":"%s","decisionDate":"2026-10-30","sessionDate":"2026-11-02",
                 "phase":"CONT_BUY","idempotencyKey":"%s","dryRun":true,"costReserveRate":0.004,
                 "orders":[{"seq":1,"stockCode":"005930","side":"BUY","quantity":1,"refPrice":70000}]}
                """.formatted("a".repeat(64), "b".repeat(64)))).andExpect(status().isOk());
        ArgumentCaptor<StrategyBatchRequest> captor = ArgumentCaptor.forClass(StrategyBatchRequest.class);
        verify(batches).create(captor.capture());
        assertThat(captor.getValue().costReserveRate()).isEqualTo(0.004);
        assertThat(captor.getValue().phase()).isEqualTo("CONT_BUY");
    }
}
