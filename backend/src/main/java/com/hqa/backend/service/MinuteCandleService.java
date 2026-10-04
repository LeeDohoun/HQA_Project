package com.hqa.backend.service;

import com.hqa.backend.dto.CandleData;
import com.hqa.backend.dto.InternalMinuteCandleResponse;
import com.hqa.backend.dto.InternalMinuteCandleResponse.MinuteCandle;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.UserRepository;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Optional;
import java.util.TreeMap;
import org.springframework.stereotype.Service;

@Service
public class MinuteCandleService {

    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter HHMMSS = DateTimeFormatter.ofPattern("HHmmss");
    private static final int MAX_PAGES = 6;

    private final UserRepository userRepository;
    private final KisClient kisClient;

    public MinuteCandleService(UserRepository userRepository, KisClient kisClient) {
        this.userRepository = userRepository;
        this.kisClient = kisClient;
    }

    public InternalMinuteCandleResponse getMinuteCandles(String userId, String stockCode, LocalDate date) {
        if (stockCode == null || !stockCode.matches("[0-9]{6}")) {
            return failure(stockCode, date, "INVALID_STOCK_CODE", 0);
        }
        LocalDate today = LocalDate.now(KST);
        if (date == null) return failure(stockCode, null, "INVALID_DATE", 0);
        if (date.isAfter(today)) return failure(stockCode, date, "DATE_IN_FUTURE", 0);
        if (date.isBefore(today.minusDays(366))) return failure(stockCode, date, "DATE_TOO_OLD", 0);

        Optional<User> userOpt = userRepository.findByUserId(userId);
        if (userOpt.isEmpty()) return failure(stockCode, date, "USER_NOT_FOUND", 0);
        UserSecret secret = userOpt.get().getSecret();
        if (secret != null && secret.isKisIsReal()) {
            return failure(stockCode, date, "PAPER_ACCOUNT_REQUIRED", 0);
        }
        if (secret == null || isBlank(secret.getKisAppKey()) || isBlank(secret.getKisAppSecret())
                || isBlank(secret.getKisAccountNo()) || isBlank(secret.getKisAccountProductCode())) {
            return failure(stockCode, date, "KIS_SECRET_MISSING", 0);
        }
        String token = kisClient.fetchAccessToken(userId, secret);
        if (isBlank(token)) return failure(stockCode, date, "KIS_TOKEN_UNAVAILABLE", 0);

        long open = date.atTime(9, 0).atZone(KST).toEpochSecond();
        long close = date.atTime(15, 30).atZone(KST).toEpochSecond();
        long oldest = close + 1;
        String cursor = "153000";
        TreeMap<Long, MinuteCandle> candles = new TreeMap<>();
        for (int pages = 1; pages <= MAX_PAGES; pages++) {
            List<CandleData> page = kisClient.fetchDailyMinuteCandles(userId, secret, token, stockCode, date, cursor);
            // KisClient also returns an empty list for upstream errors; never call that a successful session.
            if (page == null || page.isEmpty()) {
                return failure(stockCode, date, "MINUTE_CANDLES_UNAVAILABLE", pages);
            }
            for (CandleData candle : page) {
                if (candle.time() >= open && candle.time() <= close) {
                    candles.putIfAbsent(candle.time(), MinuteCandle.from(candle));
                }
            }
            if (candles.isEmpty()) return failure(stockCode, date, "MINUTE_CANDLES_UNAVAILABLE", pages);
            long first = candles.firstKey();
            if (first <= open || first >= oldest) {
                return new InternalMinuteCandleResponse(stockCode, date, "OK", List.copyOf(candles.values()), "kis", pages);
            }
            oldest = first;
            LocalTime before = Instant.ofEpochSecond(first - 1).atZone(KST).toLocalTime();
            cursor = before.format(HHMMSS);
        }
        return failure(stockCode, date, "PAGE_LIMIT_REACHED", MAX_PAGES);
    }

    private static InternalMinuteCandleResponse failure(String stockCode, LocalDate date, String status, int pages) {
        return new InternalMinuteCandleResponse(stockCode, date, status, List.of(), "kis", pages);
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
