package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;

import java.time.OffsetDateTime;
import org.junit.jupiter.api.Test;

class KrxSessionCalendarTest {
    private static OffsetDateTime kst(String local) {
        return OffsetDateTime.parse(local + "+09:00");
    }

    @Test
    void regularSessionIsWeekdaysNineToHalfPastThree() {
        KrxSessionCalendar calendar = KrxSessionCalendar.regular();
        assertThat(calendar.isOpen(kst("2026-10-12T09:00:00"))).isTrue();
        assertThat(calendar.isOpen(kst("2026-10-12T15:29:59"))).isTrue();
        assertThat(calendar.isOpen(kst("2026-10-12T15:30:00"))).isFalse();
        assertThat(calendar.isOpen(kst("2026-10-10T10:00:00"))).isFalse();  // Saturday
        assertThat(calendar.isOpen(OffsetDateTime.parse("2026-10-12T01:00:00Z"))).isTrue();  // 10:00 KST
    }

    @Test
    void configuredClosuresAndSpecialSessionsOverrideTheRegularHours() {
        KrxSessionCalendar calendar = new KrxSessionCalendar("2026-12-25, 2026-12-31", "2026-11-19@10:00-16:30");
        assertThat(calendar.isOpen(kst("2026-12-25T10:00:00"))).isFalse();
        assertThat(calendar.isOpen(kst("2026-11-19T09:30:00"))).isFalse();  // CSAT day opens at 10:00
        assertThat(calendar.isOpen(kst("2026-11-19T16:15:00"))).isTrue();   // and closes at 16:30
        assertThat(calendar.isOpen(kst("2026-11-19T16:30:00"))).isFalse();
    }

    @Test
    void malformedConfigurationFailsAtStartup() {
        assertThatThrownBy(() -> new KrxSessionCalendar("", "2026-11-19 10:00-16:30")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new KrxSessionCalendar("", "2026-11-19@16:30-10:00")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new KrxSessionCalendar("2026-13-01", "")).isInstanceOf(RuntimeException.class);
    }
}
