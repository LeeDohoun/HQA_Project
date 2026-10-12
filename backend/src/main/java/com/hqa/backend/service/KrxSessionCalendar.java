package com.hqa.backend.service;

import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * KRX regular-session hours for PAPER order gating: weekdays 09:00-15:30 Asia/Seoul,
 * minus configured weekday closures, with configured special sessions (official KRX
 * notices such as CSAT days, 10:00-16:30). Keep these in step with the Python
 * calendar (src/runner/trading_calendar.py), which also gates the monitor.
 */
@Component
public class KrxSessionCalendar {
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final LocalTime OPEN = LocalTime.of(9, 0);
    private static final LocalTime CLOSE = LocalTime.of(15, 30);
    /**
     * Weekday closures of src/runner/trading_calendar.py from 2026-10 through its review horizon
     * (CALENDAR_REVIEWED_THROUGH = 2027-09-30); a Python test keeps the two lists equal. Used when
     * HQA_KRX_CLOSED_DATES is unset or blank (Spring passes a defined-but-empty value, not its default).
     */
    public static final String DEFAULT_CLOSED_DATES = "2026-10-05,2026-10-09,2026-12-25,2026-12-31,2027-01-01,"
            + "2027-02-08,2027-02-09,2027-03-01,2027-05-05,2027-05-13,2027-08-16,2027-09-14,2027-09-15,2027-09-16";
    private final Set<LocalDate> closed = new HashSet<>();
    private final Map<LocalDate, LocalTime[]> special = new HashMap<>();

    /**
     * @param closedDates     comma-separated YYYY-MM-DD weekday closures
     * @param specialSessions comma-separated YYYY-MM-DD@HH:MM-HH:MM (e.g. 2026-11-19@10:00-16:30)
     */
    public KrxSessionCalendar(@Value("${hqa.krx-closed-dates:}") String closedDates,
                              @Value("${hqa.krx-special-sessions:}") String specialSessions) {
        for (String raw : split(closedDates == null || closedDates.isBlank() ? DEFAULT_CLOSED_DATES : closedDates)) {
            closed.add(LocalDate.parse(raw));
        }
        for (String raw : split(specialSessions)) {
            String[] dayAndHours = raw.split("@");
            String[] hours = dayAndHours.length == 2 ? dayAndHours[1].split("-") : new String[0];
            if (hours.length != 2) throw new IllegalArgumentException("hqa.krx-special-sessions entry must be YYYY-MM-DD@HH:MM-HH:MM: " + raw);
            LocalTime open = LocalTime.parse(hours[0]);
            LocalTime close = LocalTime.parse(hours[1]);
            if (!open.isBefore(close)) throw new IllegalArgumentException("special session must open before it closes: " + raw);
            special.put(LocalDate.parse(dayAndHours[0]), new LocalTime[] {open, close});
        }
    }

    public static KrxSessionCalendar regular() {
        return new KrxSessionCalendar("", "");
    }

    public boolean isOpen(OffsetDateTime at) {
        var local = at.atZoneSameInstant(KST);
        LocalDate day = local.toLocalDate();
        if (local.getDayOfWeek() == DayOfWeek.SATURDAY || local.getDayOfWeek() == DayOfWeek.SUNDAY || closed.contains(day)) {
            return false;
        }
        LocalTime[] hours = special.getOrDefault(day, new LocalTime[] {OPEN, CLOSE});
        LocalTime time = local.toLocalTime();
        return !time.isBefore(hours[0]) && time.isBefore(hours[1]);
    }

    private static String[] split(String value) {
        return value == null || value.isBlank() ? new String[0] : value.trim().split("\\s*,\\s*");
    }
}
