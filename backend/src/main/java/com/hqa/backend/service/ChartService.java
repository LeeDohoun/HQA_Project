package com.hqa.backend.service;

import com.hqa.backend.dto.CandleData;
import com.hqa.backend.dto.CandleHistoryResponse;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.exception.ApiException;
import jakarta.servlet.http.HttpSession;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class ChartService {

    // 백엔드가 노출하는 timeframe -> 1분봉 몇 개를 한 캔들로 묶을지.
    // KIS는 1분봉만 제공하므로 그 외 단위는 모두 클라이언트(이 서버) 측에서 집계.
    private static final Map<String, Integer> MINUTE_BUCKETS = Map.of(
            "1m", 1,
            "3m", 3,
            "5m", 5,
            "10m", 10,
            "15m", 15,
            "30m", 30,
            "45m", 45,
            "1h", 60
    );

    // 지원하는 일봉/주봉/월봉/년봉 timeframe -> KIS FID_PERIOD_DIV_CODE.
    private static final Map<String, String> DAILY_PERIOD_CODES = Map.of(
            "1d", "D",
            "1w", "W",
            "1M", "M",
            "1y", "Y"
    );

    private static final ZoneId KIS_ZONE = ZoneId.of("Asia/Seoul");
    // inquire-time-dailychartprice 한 번 호출당 최대 120개 1분봉.
    private static final int MINUTE_PAGE_SIZE = 120;
    // 한 요청에서 더 거슬러 올라가며 호출할 최대 횟수. 사용자 count가 클 때 안전 상한.
    private static final int MAX_MINUTE_PAGES = 20;

    private final AuthService authService;
    private final KisClient kisClient;

    public ChartService(AuthService authService, KisClient kisClient) {
        this.authService = authService;
        this.kisClient = kisClient;
    }

    public CandleHistoryResponse getHistoricalCandles(String stockCode, String timeframe,
                                                      int count, Long before, HttpSession session) {
        if (!stockCode.matches("^\\d{6}$")) {
            throw new ApiException(ErrorCode.STOCK_INVALID_CODE, 400, "Stock code must be 6 digits", null);
        }
        if (!MINUTE_BUCKETS.containsKey(timeframe) && !DAILY_PERIOD_CODES.containsKey(timeframe)) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "Unsupported timeframe", timeframe);
        }
        if (count <= 0) count = 120;
        if (count > 600) count = 600;

        User user = authService.requireUser(session);
        UserSecret secret = user.getSecret();
        if (secret == null) {
            throw new ApiException(ErrorCode.KIS_SECRET_NOT_CONFIGURED, 400,
                    "KIS credentials are not configured", null);
        }
        String userId = user.getUserId();

        String token = kisClient.fetchAccessToken(userId, secret);
        if (token == null) {
            // 자격증명은 등록되어 있는데 KIS 토큰 발급 자체가 실패한 경우.
            // KIS_SECRET_NOT_CONFIGURED는 "키를 안 넣었다"는 별도 의미라 혼동을 피해 EXTERNAL_API_ERROR로.
            throw new ApiException(ErrorCode.CHART_LOAD_FAILED, 502,
                    "Failed to obtain KIS access token. Check error_logs for details "
                            + "(rate limit, invalid credentials, or network).", null);
        }

        List<CandleData> candles = DAILY_PERIOD_CODES.containsKey(timeframe)
                ? fetchDailySeries(userId, secret, token, stockCode, timeframe, count, before)
                : fetchMinuteSeries(userId, secret, token, stockCode, timeframe, count, before);

        return new CandleHistoryResponse(stockCode, timeframe, candles, candles.size() >= count);
    }

    /**
     * 1분봉을 충분히 받아온 다음 timeframe(분 단위)로 버킷화한다.
     *
     * before가 null이면 "오늘 장중 마감까지의 분봉"을, 값이 있으면 그 시각 이전의 분봉을 끌어오기 위해
     * inquire-time-dailychartprice를 페이지네이션해서 호출한다. 같은 endpoint를 반복 호출할 때는
     * 가장 오래된 분봉의 시각보다 1분 더 이른 시각으로 다시 질의해 중복을 피한다.
     */
    private List<CandleData> fetchMinuteSeries(String userId, UserSecret secret, String token,
                                               String stockCode, String timeframe,
                                               int count, Long before) {
        int bucketMinutes = MINUTE_BUCKETS.get(timeframe);
        int targetMinutes = count * bucketMinutes;

        // 시작 커서: before가 있으면 그 시각, 없으면 KIS 기준 "지금".
        LocalDateTime cursor = before != null
                ? LocalDateTime.ofInstant(Instant.ofEpochSecond(before), KIS_ZONE)
                : LocalDateTime.now(KIS_ZONE);

        List<CandleData> collected = new ArrayList<>();
        for (int page = 0; page < MAX_MINUTE_PAGES && collected.size() < targetMinutes; page++) {
            LocalDate date = cursor.toLocalDate();
            String hour = String.format("%02d%02d%02d",
                    cursor.getHour(), cursor.getMinute(), cursor.getSecond());

            List<CandleData> chunk = kisClient.fetchDailyMinuteCandles(
                    userId, secret, token, stockCode, date, hour);
            if (chunk.isEmpty()) break;

            // chunk는 oldest-first. collected 앞에 붙이기 위해 임시로 합쳐서 정렬.
            collected.addAll(0, chunk);

            // 다음 페이지: 가장 오래된 분봉 1분 전.
            CandleData oldest = chunk.get(0);
            LocalDateTime oldestTime = LocalDateTime.ofInstant(
                    Instant.ofEpochSecond(oldest.time()), KIS_ZONE);
            LocalDateTime nextCursor = oldestTime.minusMinutes(1);
            if (!nextCursor.isBefore(cursor)) break; // 안전장치: 진행이 안 되면 중단
            cursor = nextCursor;

            if (chunk.size() < MINUTE_PAGE_SIZE) break; // 더 이상 데이터 없음
        }

        if (collected.isEmpty()) return List.of();
        // 정렬 안정성 확보 (중복 페이지가 섞일 수 있는 극단 케이스 대비).
        collected.sort((a, b) -> Long.compare(a.time(), b.time()));
        collected = dedupeByTime(collected);

        List<CandleData> bucketed = bucketMinutes == 1
                ? collected
                : bucket(collected, bucketMinutes);

        // 가장 최근 count개만 반환.
        if (bucketed.size() > count) {
            return new ArrayList<>(bucketed.subList(bucketed.size() - count, bucketed.size()));
        }
        return bucketed;
    }

    /**
     * 일/주/월/년봉. KIS는 한 호출당 최대 100건. count가 100을 넘으면 페이지네이션.
     * before는 이 시각 *이전*의 봉만 원한다는 의미.
     */
    private List<CandleData> fetchDailySeries(String userId, UserSecret secret, String token,
                                              String stockCode, String timeframe,
                                              int count, Long before) {
        String periodCode = DAILY_PERIOD_CODES.get(timeframe);
        LocalDate toCursor = before != null
                ? LocalDateTime.ofInstant(Instant.ofEpochSecond(before), KIS_ZONE).toLocalDate().minusDays(1)
                : LocalDate.now(KIS_ZONE);

        List<CandleData> collected = new ArrayList<>();
        for (int page = 0; page < 10 && collected.size() < count; page++) {
            // 100개 윈도우는 캘린더 일수가 아니라 거래일이므로 넉넉히 200일을 뒤로 잡는다.
            LocalDate fromCursor = toCursor.minusDays(200L);
            List<CandleData> chunk = kisClient.fetchDailyCandles(
                    userId, secret, token, stockCode, periodCode, fromCursor, toCursor);
            if (chunk.isEmpty()) break;
            collected.addAll(0, chunk);
            CandleData oldest = chunk.get(0);
            LocalDate oldestDate = LocalDateTime.ofInstant(
                    Instant.ofEpochSecond(oldest.time()), KIS_ZONE).toLocalDate();
            LocalDate next = oldestDate.minusDays(1);
            if (!next.isBefore(toCursor)) break;
            toCursor = next;
            if (chunk.size() < 100) break;
        }

        if (collected.isEmpty()) return List.of();
        collected.sort((a, b) -> Long.compare(a.time(), b.time()));
        collected = dedupeByTime(collected);
        if (collected.size() > count) {
            return new ArrayList<>(collected.subList(collected.size() - count, collected.size()));
        }
        return collected;
    }

    private static List<CandleData> dedupeByTime(List<CandleData> sorted) {
        if (sorted.size() <= 1) return sorted;
        List<CandleData> out = new ArrayList<>(sorted.size());
        long lastTime = Long.MIN_VALUE;
        for (CandleData c : sorted) {
            if (c.time() != lastTime) {
                out.add(c);
                lastTime = c.time();
            }
        }
        return out;
    }

    /**
     * 1분봉을 N분봉으로 묶는다. 버킷 경계는 자정 기준 N분 그리드에 맞춰 floor.
     */
    private static List<CandleData> bucket(List<CandleData> oneMin, int n) {
        if (oneMin.isEmpty()) return Collections.emptyList();
        long bucketSec = n * 60L;
        List<CandleData> out = new ArrayList<>();
        long bucketStart = Long.MIN_VALUE;
        double open = 0, high = 0, low = 0, close = 0;
        long volume = 0;
        for (CandleData c : oneMin) {
            long start = (c.time() / bucketSec) * bucketSec;
            if (start != bucketStart) {
                if (bucketStart != Long.MIN_VALUE) {
                    out.add(new CandleData(bucketStart, open, high, low, close, volume, Boolean.TRUE));
                }
                bucketStart = start;
                open = c.open();
                high = c.high();
                low = c.low();
                close = c.close();
                volume = c.volume();
            } else {
                high = Math.max(high, c.high());
                low = Math.min(low, c.low());
                close = c.close();
                volume += c.volume();
            }
        }
        if (bucketStart != Long.MIN_VALUE) {
            out.add(new CandleData(bucketStart, open, high, low, close, volume, Boolean.TRUE));
        }
        return out;
    }
}
