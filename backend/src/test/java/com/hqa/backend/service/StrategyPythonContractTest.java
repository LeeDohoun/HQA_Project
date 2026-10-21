package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.hqa.backend.dto.StrategyBatchRequest;
import java.io.InputStream;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * The batch JSON produced by the Python strategy runtime (tests/test_live_contract.py writes
 * src/test/resources/strategy/python_batch_payload.json) must pass the backend's validation
 * unchanged, read with the production Jackson settings (SNAKE_CASE default naming).
 */
class StrategyPythonContractTest {
    private final StrategyTestFixture f = new StrategyTestFixture();
    private final ObjectMapper production = new ObjectMapper().registerModule(new JavaTimeModule())
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);

    private StrategyBatchRequest pythonPayload() throws Exception {
        try (InputStream in = getClass().getResourceAsStream("/strategy/python_batch_payload.json")) {
            assertThat(in).as("run tests/test_live_contract.py to generate the payload").isNotNull();
            return production.readValue(in, StrategyBatchRequest.class);
        }
    }

    @Test
    void pythonBatchPayloadDeserializesAndValidates() throws Exception {
        StrategyBatchRequest request = pythonPayload();
        assertThat(request.strategyId()).isEqualTo("hf001_f_both");
        assertThat(request.orders()).hasSize(12);
        assertThat(request.orders()).anySatisfy(order -> assertThat(order.stockCode()).isEqualTo("0126Z0"));
        assertThat(request.idempotencyKey()).isEqualTo(StrategyBatchService.idempotencyKey(
                request.strategyId(), request.decisionHash(), request.sessionDate().toString(), request.phase(),
                request.userId()));

        Map<String, Object> response = f.service.create(request);

        assertThat(response.get("reasons")).isEqualTo(List.of());
        assertThat(response.get("status")).isEqualTo("VALIDATED");
        assertThat(response.get("orderCount")).isEqualTo(12);
    }

    @Test
    void pythonContBuyPayloadValidatesWithItsCostReserve() throws Exception {
        StrategyBatchRequest request;
        try (InputStream in = getClass().getResourceAsStream("/strategy/python_cont_buy_payload.json")) {
            assertThat(in).as("run tests/test_live_contract.py to generate the payload").isNotNull();
            request = production.readValue(in, StrategyBatchRequest.class);
        }
        assertThat(request.phase()).isEqualTo("CONT_BUY");
        assertThat(request.costReserveRate()).isNotNull().isBetween(0.0, 0.05);
        assertThat(request.orders()).allSatisfy(order -> assertThat(order.side()).isEqualTo("BUY"));

        Map<String, Object> response = f.service.create(request);

        assertThat(response.get("reasons")).isEqualTo(List.of());
        assertThat(response.get("status")).isEqualTo("VALIDATED");
    }
}
