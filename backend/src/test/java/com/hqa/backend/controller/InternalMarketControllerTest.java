package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.InternalMinuteCandleRequest;
import com.hqa.backend.dto.InternalMinuteCandleResponse;
import com.hqa.backend.dto.InternalPriceSnapshotRequest;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.PriceSnapshotService;
import com.hqa.backend.service.MinuteCandleService;
import java.time.LocalDate;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class InternalMarketControllerTest {

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = "bad-token")
    void minuteCandlesRejectsInvalidInternalToken(String token) {
        HqaProperties properties = new HqaProperties();
        properties.setInternalToken("expected-token");
        MinuteCandleService minutes = mock(MinuteCandleService.class);
        InternalMarketController controller = new InternalMarketController(mock(PriceSnapshotService.class), properties);
        controller.setMinuteCandleService(minutes);
        assertThatThrownBy(() -> controller.minuteCandles(
                new InternalMinuteCandleRequest("user-1", "005930", LocalDate.of(2026, 9, 30)), token))
                .isInstanceOf(ApiException.class).hasMessage("Invalid internal token");
        verifyNoInteractions(minutes);
    }

    @Test
    void minuteCandlesRejectsUnconfiguredInternalToken() {
        InternalMarketController controller = new InternalMarketController(mock(PriceSnapshotService.class), new HqaProperties());
        assertThatThrownBy(() -> controller.minuteCandles(
                new InternalMinuteCandleRequest("user-1", "005930", LocalDate.of(2026, 9, 30)), ""))
                .isInstanceOf(ApiException.class).hasMessage("Invalid internal token");
    }

    @Test
    void minuteCandlesPreservesCamelCaseContractAndValidatesBody() throws Exception {
        HqaProperties properties = new HqaProperties();
        properties.setInternalToken("expected-token");
        MinuteCandleService minutes = mock(MinuteCandleService.class);
        InternalMarketController controller = new InternalMarketController(mock(PriceSnapshotService.class), properties);
        controller.setMinuteCandleService(minutes);
        LocalDate date = LocalDate.of(2026, 9, 30);
        when(minutes.getMinuteCandles("user-1", "005930", date)).thenReturn(new InternalMinuteCandleResponse(
                "005930", date, "OK", List.of(new InternalMinuteCandleResponse.MinuteCandle(1790726400L, 100, 110, 90, 105, 20)), "kis", 4));
        ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule())
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
        var mvc = MockMvcBuilders.standaloneSetup(controller)
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();
        mvc.perform(post("/api/v1/internal/market/minute-candles").header("X-HQA-Internal-Token", "expected-token")
                .contentType("application/json").content("{\"userId\":\"user-1\",\"stockCode\":\"005930\",\"date\":\"2026-09-30\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.stockCode").value("005930"))
                .andExpect(jsonPath("$.date").value("2026-09-30"))
                .andExpect(jsonPath("$.status").value("OK")).andExpect(jsonPath("$.source").value("kis"))
                .andExpect(jsonPath("$.pages").value(4)).andExpect(jsonPath("$.candles[0].volume").value(20))
                .andExpect(jsonPath("$.candles[0].complete").doesNotExist());
        mvc.perform(post("/api/v1/internal/market/minute-candles").header("X-HQA-Internal-Token", "expected-token")
                .contentType("application/json").content("{\"userId\":\"user-1\",\"stockCode\":\"../x\",\"date\":\"2026-09-30\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void priceSnapshotsRejectsInvalidInternalToken() {
        HqaProperties properties = new HqaProperties();
        properties.setInternalToken("expected-token");
        InternalMarketController controller = new InternalMarketController(
                mock(PriceSnapshotService.class),
                properties
        );

        assertThatThrownBy(() -> controller.priceSnapshots(
                new InternalPriceSnapshotRequest("user-1", List.of("005930")),
                "bad-token"
        ))
                .isInstanceOf(ApiException.class)
                .hasMessage("Invalid internal token");
    }
}
