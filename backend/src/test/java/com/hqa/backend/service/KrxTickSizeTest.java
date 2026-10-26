package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class KrxTickSizeTest {
    @Test
    void theUnitChangesAtEachBandOfTheKrxTable() {
        long[][] units = {{1, 1}, {1_999, 1}, {2_000, 5}, {4_999, 5}, {5_000, 10}, {19_999, 10}, {20_000, 50},
                {49_999, 50}, {50_000, 100}, {199_999, 100}, {200_000, 500}, {499_999, 500}, {500_000, 1_000},
                {2_500_000, 1_000}};
        for (long[] unit : units) assertThat(KrxTickSize.of(unit[0])).as("unit at %d", unit[0]).isEqualTo(unit[1]);
        assertThatThrownBy(() -> KrxTickSize.of(0)).isInstanceOf(IllegalArgumentException.class);
        assertThat(KrxTickSize.isValid(0)).isFalse();
    }

    @Test
    void anOffGridQuoteRoundsUpForABuyAndDownForASell() {
        assertThat(KrxTickSize.isValid(406_250)).isFalse();             // 373220's KIS paper quote on 2026-10-16
        assertThat(KrxTickSize.roundUp(406_250)).isEqualTo(406_500);
        assertThat(KrxTickSize.roundDown(406_250)).isEqualTo(406_000);
        assertThat(KrxTickSize.roundUp(406_500)).isEqualTo(406_500);    // a valid price stays
        assertThat(KrxTickSize.roundDown(406_500)).isEqualTo(406_500);
        assertThat(KrxTickSize.roundUp(499_750)).isEqualTo(500_000);    // up into the next band
        assertThat(KrxTickSize.roundDown(500_999)).isEqualTo(500_000);
        assertThat(KrxTickSize.roundUp(4_997)).isEqualTo(5_000);
        assertThat(KrxTickSize.roundDown(2_003)).isEqualTo(2_000);
        assertThat(KrxTickSize.roundUp(1_999)).isEqualTo(1_999);
    }

    @Test
    void roundingReachesTheNearestValidPriceInEitherDirection() {
        // Step through the valid prices one unit at a time and check every price up to 600,000 against them.
        List<Long> wrong = new ArrayList<>();
        long below = 0;
        long next = 1;
        for (long price = 1; price <= 600_000; price++) {
            if (price > next) {
                below = next;
                next += KrxTickSize.of(next);
            }
            if (KrxTickSize.roundUp(price) != next || KrxTickSize.roundDown(price) != (price == next ? next : below)
                    || KrxTickSize.isValid(price) != (price == next)) {
                wrong.add(price);
            }
        }
        assertThat(wrong).isEmpty();
    }
}
