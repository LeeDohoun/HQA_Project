package com.hqa.backend.util;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class StockCodesTest {
    @ParameterizedTest
    @ValueSource(strings = {"0015G0", "005930", "0041B0", "0001A0", "00088K", "ABCDEF"})
    void acceptsKrxShortCodes(String code) {
        assertThat(StockCodes.isValid(code)).isTrue();
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"0015g0", "15G0", "0015G0X", "0015-0", " 005930", "005930\n", "００５９３０", "0015Ｇ0"})
    void rejectsInvalidCodes(String code) {
        assertThat(StockCodes.isValid(code)).isFalse();
    }
}
