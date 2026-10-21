package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;

import com.hqa.backend.config.StrategyProperties;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import org.junit.jupiter.api.Test;

class StrategySessionClockTest {
    private final StrategyProperties properties = new StrategyProperties();
    private final StrategySessionClock sessions = new StrategySessionClock(
            new KrxSessionCalendar("", "2026-11-19@10:00-16:30"), properties);
    private static final LocalDate DAY = LocalDate.parse("2026-11-02");

    private static OffsetDateTime at(String value) {
        return OffsetDateTime.parse(value);
    }

    @Test
    void windowsIncludeTheStartAndExcludeTheEnd() {
        assertThat(sessions.inPhaseWindow(DAY, "OPEN", at("2026-11-02T08:29:59+09:00"))).isFalse();
        assertThat(sessions.inPhaseWindow(DAY, "OPEN", at("2026-11-02T08:30:00+09:00"))).isTrue();
        assertThat(sessions.inPhaseWindow(DAY, "OPEN", at("2026-11-02T08:58:59+09:00"))).isTrue();
        assertThat(sessions.inPhaseWindow(DAY, "OPEN", at("2026-11-02T08:59:00+09:00"))).isFalse();
        assertThat(sessions.phaseWindowClosed(DAY, "OPEN", at("2026-11-02T08:59:00+09:00"))).isTrue();
        assertThat(sessions.inPhaseWindow(DAY, "CLOSE", at("2026-11-02T15:20:00+09:00"))).isTrue();
        assertThat(sessions.inPhaseWindow(DAY, "CLOSE", at("2026-11-02T15:29:00+09:00"))).isFalse();
        // The same instant written in UTC is compared correctly.
        assertThat(sessions.inPhaseWindow(DAY, "OPEN", at("2026-11-01T23:45:00Z"))).isTrue();
        assertThat(sessions.openCancelAt(DAY)).isEqualTo(at("2026-11-02T09:05:00+09:00"));
    }

    @Test
    void specialSessionsShiftTheWindowsAndClosedDaysHaveNone() {
        LocalDate csat = LocalDate.parse("2026-11-19");
        assertThat(sessions.inPhaseWindow(csat, "OPEN", at("2026-11-19T08:35:00+09:00"))).isFalse();
        assertThat(sessions.inPhaseWindow(csat, "OPEN", at("2026-11-19T09:35:00+09:00"))).isTrue();
        assertThat(sessions.inPhaseWindow(csat, "CLOSE", at("2026-11-19T16:25:00+09:00"))).isTrue();
        assertThat(sessions.openCancelAt(csat)).isEqualTo(at("2026-11-19T10:05:00+09:00"));
        assertThat(sessions.sessionCloseAt(csat)).isEqualTo(at("2026-11-19T16:30:00+09:00"));
        assertThat(sessions.phaseWindow(LocalDate.parse("2026-11-07"), "OPEN")).isNull();
        assertThat(sessions.isSessionDay(LocalDate.parse("2026-12-25"))).isFalse();
        assertThat(sessions.isSessionDay(DAY)).isTrue();
    }

    @Test
    void configuredWindowsAndOrderTypeAreValidated() {
        properties.setOpenWindow("08:40-08:55");
        assertThat(sessions.inPhaseWindow(DAY, "OPEN", at("2026-11-02T08:35:00+09:00"))).isFalse();
        assertThatThrownBy(() -> properties.setCloseWindow("15:29-15:20")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> properties.setOrderType("LIMIT")).isInstanceOf(IllegalArgumentException.class);
        properties.setOrderType("market");
        assertThat(properties.getOrderType()).isEqualTo("MARKET");
        assertThat(properties.isOrdersEnabled()).isFalse();
    }

    @Test
    void continuousPhasesAreOffsetsFromTheOfficialOpen() {
        assertThat(sessions.inPhaseWindow(DAY, "CONT_SELL", at("2026-11-02T09:04:59+09:00"))).isFalse();
        assertThat(sessions.inPhaseWindow(DAY, "CONT_SELL", at("2026-11-02T09:05:00+09:00"))).isTrue();
        assertThat(sessions.inPhaseWindow(DAY, "CONT_SELL", at("2026-11-02T09:20:00+09:00"))).isFalse();
        assertThat(sessions.inPhaseWindow(DAY, "CONT_BUY", at("2026-11-02T09:20:00+09:00"))).isTrue();
        assertThat(sessions.inPhaseWindow(DAY, "CONT_BUY", at("2026-11-02T10:00:00+09:00"))).isFalse();
        assertThat(sessions.cancelAt(DAY, "CONT_SELL")).isEqualTo(at("2026-11-02T09:20:00+09:00"));
        assertThat(sessions.cancelAt(DAY, "CONT_BUY")).isEqualTo(at("2026-11-02T10:00:00+09:00"));
        assertThat(sessions.cancelAt(DAY, "OPEN")).isEqualTo(at("2026-11-02T09:05:00+09:00"));
        assertThat(sessions.cancelAt(DAY, "CLOSE")).isNull();
        assertThat(sessions.navCloseRecordAt(DAY)).isEqualTo(at("2026-11-02T15:45:00+09:00"));

        // CSAT day, official 10:00-16:30: open +5..+20 sell, +20..+60 buy, +60 cancel; close -10..-1 exits.
        LocalDate csat = LocalDate.parse("2026-11-19");
        assertThat(sessions.phaseWindow(csat, "CONT_SELL")).containsExactly(at("2026-11-19T10:05:00+09:00"), at("2026-11-19T10:20:00+09:00"));
        assertThat(sessions.phaseWindow(csat, "CONT_BUY")).containsExactly(at("2026-11-19T10:20:00+09:00"), at("2026-11-19T11:00:00+09:00"));
        assertThat(sessions.cancelAt(csat, "CONT_BUY")).isEqualTo(at("2026-11-19T11:00:00+09:00"));
        assertThat(sessions.cancelAt(csat, "CONT_SELL")).isEqualTo(at("2026-11-19T10:20:00+09:00"));
        assertThat(sessions.phaseWindow(csat, "CLOSE")).containsExactly(at("2026-11-19T16:20:00+09:00"), at("2026-11-19T16:29:00+09:00"));
        assertThat(sessions.navCloseRecordAt(csat)).isEqualTo(at("2026-11-19T16:45:00+09:00"));
        assertThat(sessions.phaseWindow(DAY, "MIDDAY")).isNull();
    }
}
