package com.hqa.backend.dto;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.validation.Validation;
import jakarta.validation.Validator;
import java.time.LocalDate;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class InternalMinuteCandleRequestValidationTest {

    @ParameterizedTest
    @ValueSource(strings = {"0041B0", "005930", "0015G0"})
    void acceptsValidKrxShortCodes(String stockCode) {
        try (var factory = Validation.buildDefaultValidatorFactory()) {
            Validator validator = factory.getValidator();
            var request = new InternalMinuteCandleRequest("user-1", stockCode, LocalDate.of(2026, 9, 30));

            assertThat(validator.validate(request)).isEmpty();
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"0041b0", "41B0", "0041B0X", "0015g0", "15G0", "0015G0X", "0015-0"})
    void rejectsInvalidKrxShortCodes(String stockCode) {
        try (var factory = Validation.buildDefaultValidatorFactory()) {
            Validator validator = factory.getValidator();
            var request = new InternalMinuteCandleRequest("user-1", stockCode, LocalDate.of(2026, 9, 30));

            assertThat(validator.validate(request))
                    .extracting(violation -> violation.getPropertyPath().toString())
                    .containsExactly("stockCode");
        }
    }
}
