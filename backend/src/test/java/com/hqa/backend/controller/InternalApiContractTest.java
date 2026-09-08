package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.*;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.service.*;
import java.time.OffsetDateTime;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class InternalApiContractTest {
    final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule())
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    final HqaProperties props = new HqaProperties();
    { props.setInternalToken("contract-test"); }

    @Test
    void pythonCamelCaseRequestsWorkWithTheProductionSnakeCaseMapper() throws Exception {
        var accounts = mock(PaperAccountSnapshotService.class);
        var prices = mock(PriceSnapshotService.class);
        when(accounts.snapshot("u1")).thenReturn(Map.of("userId", "u1", "success", true));
        when(prices.getSnapshots("u1", List.of("005930"))).thenReturn(List.of(new PriceSnapshotResponse(
                "005930", 70000L, OffsetDateTime.parse("2026-09-08T10:00:00+09:00"), "kis", true, null)));
        var mvc = MockMvcBuilders.standaloneSetup(new InternalAccountSnapshotController(accounts, props),
                new InternalMarketController(prices, props))
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();
        mvc.perform(post("/api/v1/internal/trading/account-snapshots").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("{\"userIds\":[\"u1\"]}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.snapshots[0].userId").value("u1"));
        mvc.perform(post("/api/v1/internal/market/price-snapshots").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("{\"userId\":\"u1\",\"stockCodes\":[\"005930\"]}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.snapshots[0].currentPrice").value(70000))
                .andExpect(jsonPath("$.snapshots[0].snapshotAt").value("2026-09-08T10:00:00+09:00"));
    }

    @Test
    void signalPlansPreserveTheirIdentityAndVersionOverHttp() throws Exception {
        var signals = mock(TradeSignalService.class); var signal = new TradeSignal(); signal.onCreate();
        when(signals.saveSignal(any())).thenReturn(signal);
        var mvc = MockMvcBuilders.standaloneSetup(new InternalTradeSignalController(signals, props))
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();
        mvc.perform(post("/api/v1/internal/trading/signals").header("X-HQA-Internal-Token", "contract-test")
                .contentType("application/json").content("""
                {"userId":"u1","source":"test","stockCode":"005930","stockName":"삼성전자","action":"BUY",
                 "planVersion":2,"accountMode":"PAPER","analysisId":"analysis-1","targetPositionPct":10.5,
                 "analysisAsOf":"2026-09-08T10:00:00+09:00","conditionPayload":{"schema_version":2}}
                """))
                .andExpect(status().isOk()).andExpect(jsonPath("$.signalId").value(signal.getId()));
        var captor = ArgumentCaptor.forClass(InternalTradeSignalRequest.class); verify(signals).saveSignal(captor.capture());
        assertThat(captor.getValue().planVersion()).isEqualTo(2);
        assertThat(captor.getValue().analysisId()).isEqualTo("analysis-1");
        assertThat(captor.getValue().conditionPayload()).containsEntry("schema_version", 2);
    }

    @Test
    void publicOrderPayloadUsesSnakeCaseAndKeepsTheLimitPrice() throws Exception {
        var order = mapper.readValue("""
                {"stock_name":"삼성전자","stock_code":"005930","quantity":3,"limit_price":71000}
                """, DirectBuyRequest.class);
        assertThat(order.getStockCode()).isEqualTo("005930");
        assertThat(order.getLimitPrice()).isEqualTo(71000);
        assertThat(order.getQuantity()).isEqualTo(3);
    }
}
