package com.hqa.backend.service;

import com.hqa.backend.config.StrategyProperties;
import java.time.Clock;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * KST session and auction-phase times for strategy batches. The configured windows are for a regular
 * session (09:00-15:30); on an official special session the OPEN window and the open cancel time move
 * with the session's open, and the CLOSE window with its close. Window ends are exclusive.
 */
@Component
public class StrategySessionClock {
    public static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final LocalTime REGULAR_OPEN = LocalTime.of(9, 0);
    private static final LocalTime REGULAR_CLOSE = LocalTime.of(15, 30);
    /** After this long past the close, unfilled day orders are treated as expired at the broker. */
    public static final Duration CLOSE_SETTLE = Duration.ofMinutes(10);
    private final KrxSessionCalendar calendar;
    private final StrategyProperties properties;
    private final Clock clock;

    @Autowired
    public StrategySessionClock(KrxSessionCalendar calendar, StrategyProperties properties) {
        this(calendar, properties, Clock.system(KST));
    }

    public StrategySessionClock(KrxSessionCalendar calendar, StrategyProperties properties, Clock clock) {
        this.calendar = calendar;
        this.properties = properties;
        this.clock = clock;
    }

    public OffsetDateTime now() {
        return OffsetDateTime.now(clock).atZoneSameInstant(KST).toOffsetDateTime();
    }

    public LocalDate today() {
        return now().toLocalDate();
    }

    public boolean isSessionDay(LocalDate day) {
        return calendar.isSessionDay(day);
    }

    /**
     * Fail-closed calendar check for strategy batches: false after the reviewed calendar horizon and on
     * an announced special-session date whose hours are not configured yet. The LLM path is unaffected.
     */
    public boolean calendarVerified(LocalDate day) {
        return !day.isAfter(properties.calendarReviewedThroughDate())
                && (!properties.pendingSpecialSessionDates().contains(day) || calendar.hasSpecialSession(day));
    }

    /** [start, end) of the phase window, or null when the day is not a (verified) KRX session. */
    public OffsetDateTime[] phaseWindow(LocalDate day, String phase) {
        LocalTime[] hours = calendar.sessionHours(day);
        if (hours == null || !calendarVerified(day)) return null;
        boolean open = "OPEN".equals(phase);
        LocalTime[] window = open ? properties.openWindowTimes() : properties.closeWindowTimes();
        Duration shift = open ? Duration.between(REGULAR_OPEN, hours[0]) : Duration.between(REGULAR_CLOSE, hours[1]);
        return new OffsetDateTime[] {at(day, window[0]).plus(shift), at(day, window[1]).plus(shift)};
    }

    public boolean inPhaseWindow(LocalDate day, String phase, OffsetDateTime when) {
        OffsetDateTime[] window = phaseWindow(day, phase);
        return window != null && !when.isBefore(window[0]) && when.isBefore(window[1]);
    }

    public boolean phaseWindowClosed(LocalDate day, String phase, OffsetDateTime when) {
        OffsetDateTime[] window = phaseWindow(day, phase);
        return window == null || !when.isBefore(window[1]);
    }

    /** When the remaining quantity of an OPEN batch is cancelled; null when the day is not a session. */
    public OffsetDateTime openCancelAt(LocalDate day) {
        LocalTime[] hours = calendar.sessionHours(day);
        if (hours == null) return null;
        return at(day, properties.openCancelAfterTime()).plus(Duration.between(REGULAR_OPEN, hours[0]));
    }

    public OffsetDateTime sessionOpenAt(LocalDate day) {
        LocalTime[] hours = calendar.sessionHours(day);
        return hours == null ? null : at(day, hours[0]);
    }

    public OffsetDateTime sessionCloseAt(LocalDate day) {
        LocalTime[] hours = calendar.sessionHours(day);
        return hours == null ? null : at(day, hours[1]);
    }

    private static OffsetDateTime at(LocalDate day, LocalTime time) {
        return day.atTime(time).atZone(KST).toOffsetDateTime();
    }
}
