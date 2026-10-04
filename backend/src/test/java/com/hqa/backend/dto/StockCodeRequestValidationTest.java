package com.hqa.backend.dto;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.validation.Validation;
import java.util.List;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class StockCodeRequestValidationTest {
    @ParameterizedTest
    @ValueSource(strings = {"0015G0", "005930"})
    void acceptsKrxShortCodes(String code) {
        try (var factory = Validation.buildDefaultValidatorFactory()) {
            for (Object request : requests(code)) {
                assertThat(factory.getValidator().validate(request))
                        .as(request.getClass().getSimpleName()).isEmpty();
            }
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"0015g0", "15G0", "0015G0X", "0015-0", "005930\n", "００５９３０"})
    void rejectsInvalidCodes(String code) {
        try (var factory = Validation.buildDefaultValidatorFactory()) {
            for (Object request : requests(code)) {
                assertThat(factory.getValidator().validate(request))
                        .as(request.getClass().getSimpleName())
                        .extracting(violation -> violation.getPropertyPath().toString())
                        .containsExactly("stockCode");
            }
        }
    }

    private List<Object> requests(String code) {
        var buy = new DirectBuyRequest();
        buy.setStockName("Stock");
        buy.setStockCode(code);
        var analysis = new AnalysisRequest();
        analysis.setStockName("Stock");
        analysis.setStockCode(code);
        var decision = new TradeDecisionRequest();
        decision.setStockName("Stock");
        decision.setStockCode(code);
        decision.setFinalDecision(new TradeDecisionPayload());
        return List.of(buy, analysis, decision, new WatchlistItemRequest("Stock", code, "KOSDAQ"));
    }
}
