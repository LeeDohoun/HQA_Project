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
 * session (09:00-15:30); on an official special session the OPEN, CONT_SELL and CONT_BUY windows and
 * their cancel times move with the session's open, and the CLOSE window with its close. Window ends
 * are exclusive.
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
        LocalTime[] window = switch (phase) {
            case "OPEN" -> properties.openWindowTimes();
            case "CLOSE" -> properties.closeWindowTimes();
            case "CONT_SELL" -> properties.contSellWindowTimes();
            case "CONT_BUY" -> properties.contBuyWindowTimes();
            default -> null;
        };
        if (window == null) return null;
        // Everything but the closing auction moves with the session open.
        Duration shift = "CLOSE".equals(phase) ? Duration.between(REGULAR_CLOSE, hours[1]) : Duration.between(REGULAR_OPEN, hours[0]);
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

    /** When unfilled remainders of the phase are cancelled; null for CLOSE (they expire with the session). */
    public OffsetDateTime cancelAt(LocalDate day, String phase) {
        LocalTime[] hours = calendar.sessionHours(day);
        if (hours == null) return null;
        LocalTime time = switch (phase) {
            case "OPEN" -> properties.openCancelAfterTime();
            case "CONT_SELL" -> properties.contSellCancelAtTime();
            case "CONT_BUY" -> properties.contBuyCancelAtTime();
            default -> null;
        };
        return time == null ? null : at(day, time).plus(Duration.between(REGULAR_OPEN, hours[0]));
    }

    /** When the close-of-session NAV is recorded (moves with the session close); null when not a session. */
    public OffsetDateTime navCloseRecordAt(LocalDate day) {
        LocalTime[] hours = calendar.sessionHours(day);
        if (hours == null) return null;
        return at(day, properties.navCloseRecordAtTime()).plus(Duration.between(REGULAR_CLOSE, hours[1]));
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
