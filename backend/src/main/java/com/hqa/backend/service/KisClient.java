package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.CandleData;
import com.hqa.backend.dto.KisVerificationResult;
import com.hqa.backend.entity.UserSecret;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

@Component
public class KisClient {

    // KIS는 실전(real)과 모의(sandbox)가 분리된 별도 호스트를 사용.
    private static final String KIS_BASE_URL_REAL = "https://openapi.koreainvestment.com:9443";
    private static final String KIS_BASE_URL_SANDBOX = "https://openapivts.koreainvestment.com:29443";
    private static final String TOKEN_PATH = "/oauth2/tokenP";
    private static final String ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash";
    private static final String BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance";
    private static final String CHART_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice";
    private static final String CHART_TODAY_MINUTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice";
    private static final String CHART_DAILY_MINUTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice";

    private static final ZoneId KIS_ZONE = ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter YYYYMMDD = DateTimeFormatter.ofPattern("yyyyMMdd");
    private static final DateTimeFormatter HHMMSS = DateTimeFormatter.ofPattern("HHmmss");

    private final WebClient webClient;
    private final ObjectMapper objectMapper;
    private final ErrorLogger errorLogger;
    private final SecretCipher secretCipher;

    // KIS는 토큰 발급을 appkey당 ~1회/분으로 제한 (EGW00133)하므로 토큰을 캐시한다.
    // 키: appKey + ":" + isReal(R/S). 토큰 자체는 24h 유효하지만 안전마진을 두고 만료 10분 전부터 재발급.
    private static final Duration TOKEN_REFRESH_MARGIN = Duration.ofMinutes(10);
    private final ConcurrentHashMap<String, CachedToken> tokenCache = new ConcurrentHashMap<>();

    private record CachedToken(String value, long expiresAtEpochSec) {
        boolean isFresh(long nowEpochSec, long marginSec) {
            return value != null && !value.isBlank() && nowEpochSec + marginSec < expiresAtEpochSec;
        }
    }

    public KisClient(WebClient webClient, ObjectMapper objectMapper, ErrorLogger errorLogger,
                     SecretCipher secretCipher) {
        this.webClient = webClient;
        this.objectMapper = objectMapper;
        this.errorLogger = errorLogger;
        this.secretCipher = secretCipher;
    }

    public String fetchAccessToken(String userId, UserSecret secret) {
        return fetchToken(userId, secret);
    }

    /**
     * 회원가입·설정 화면에서 사용자가 입력한 KIS 자격증명이 실제로 KIS에서 동작하는지 확인.
     *
     * 1) /oauth2/tokenP 로 토큰 발급 시도 → App Key/Secret 유효성 확인
     * 2) /uapi/.../inquire-balance 로 계좌조회 시도 → CANO/ACNT_PRDT_CD 유효성 확인
     *
     * 두 단계 모두 성공해야 ok=true. 어느 단계에서 어떤 사유로 실패했는지 stage/message로 반환.
     *
     * 주의: 호출하는 쪽이 사용자에게 보여줄 메시지이므로 KIS의 한국어 msg1을 가능한 그대로 전달.
     */
    public KisVerificationResult verifyCredentials(String userId, String appKey, String appSecret,
                                                   String accountNo, String acntPrdtCd, boolean isReal) {
        // ── 1) 토큰 발급
        String baseUrl = isReal ? KIS_BASE_URL_REAL : KIS_BASE_URL_SANDBOX;
        String token;
        try {
            String response = webClient.post()
                    .uri(baseUrl + TOKEN_PATH)
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(Map.of(
                            "grant_type", "client_credentials",
                            "appkey", appKey,
                            "appsecret", appSecret
                    ))
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            Map<String, Object> body = objectMapper.readValue(response, new TypeReference<>() {});
            token = (String) body.get("access_token");
            if (token == null || token.isBlank()) {
                String desc = String.valueOf(body.getOrDefault("error_description", body.get("error")));
                return new KisVerificationResult(false, false, false, "token",
                        "App Key / App Secret이 올바르지 않은 것 같아요. (" + desc + ")");
            }
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, null, "verify token failed", e.getMessage());
            String hint = e.getMessage() != null && e.getMessage().contains("403")
                    ? "App Key / App Secret이 올바르지 않거나 호출 권한이 없어요."
                    : "KIS 서버 연결에 실패했어요. 잠시 후 다시 시도해주세요.";
            return new KisVerificationResult(false, false, false, "token", hint);
        }

        // ── 2) 계좌조회로 CANO·ACNT_PRDT_CD 검증
        // tr_id: 실전 TTTC8434R / 모의 VTTC8434R
        String balanceTr = isReal ? "TTTC8434R" : "VTTC8434R";
        try {
            String response = webClient.get()
                    .uri(uriBuilder -> uriBuilder.scheme("https")
                            .host(isReal ? "openapi.koreainvestment.com" : "openapivts.koreainvestment.com")
                            .port(isReal ? 9443 : 29443)
                            .path(BALANCE_PATH)
                            .queryParam("CANO", accountNo)
                            .queryParam("ACNT_PRDT_CD", acntPrdtCd)
                            .queryParam("AFHR_FLPR_YN", "N")
                            .queryParam("OFL_YN", "")
                            .queryParam("INQR_DVSN", "02")
                            .queryParam("UNPR_DVSN", "01")
                            .queryParam("FUND_STTL_ICLD_YN", "N")
                            .queryParam("FNCG_AMT_AUTO_RDPT_YN", "N")
                            .queryParam("PRCS_DVSN", "01")
                            .queryParam("CTX_AREA_FK100", "")
                            .queryParam("CTX_AREA_NK100", "")
                            .build())
                    .header("authorization", "Bearer " + token)
                    .header("appkey", appKey)
                    .header("appsecret", appSecret)
                    .header("tr_id", balanceTr)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            Map<String, Object> body = objectMapper.readValue(response, new TypeReference<>() {});
            String rtCd = String.valueOf(body.getOrDefault("rt_cd", ""));
            if (!"0".equals(rtCd)) {
                String msg1 = String.valueOf(body.getOrDefault("msg1", "계좌를 조회할 수 없어요"));
                errorLogger.log("KisClient", userId, null, "verify account rejected", response);
                return new KisVerificationResult(false, true, false, "account",
                        "계좌번호 또는 계좌상품코드가 맞지 않아요. (" + msg1.trim() + ")");
            }
            return new KisVerificationResult(true, true, true, "ok", "확인되었어요.");
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, null, "verify account failed", e.getMessage());
            return new KisVerificationResult(false, true, false, "account",
                    "계좌 조회 중 오류가 발생했어요: " + e.getMessage());
        }
    }

    /**
     * 직접 매수. limitPrice가 0이면 시장가 주문.
     * @return 성공 여부와 KIS 응답 본문을 담은 맵
     */
    public Map<String, Object> buy(String userId, UserSecret secret, String token,
                                   String stockCode, int quantity, long limitPrice) {
        return order(userId, secret, token, stockCode, quantity, limitPrice, /* isBuy = */ true);
    }

    /**
     * 직접 매도. limitPrice가 0이면 시장가 주문.
     */
    public Map<String, Object> sell(String userId, UserSecret secret, String token,
                                    String stockCode, int quantity, long limitPrice) {
        return order(userId, secret, token, stockCode, quantity, limitPrice, /* isBuy = */ false);
    }

    private Map<String, Object> order(String userId, UserSecret secret, String token,
                                      String stockCode, int quantity, long limitPrice, boolean isBuy) {
        try {
            String ordDvsn = limitPrice <= 0 ? "01" : "00"; // 01=시장가, 00=지정가
            String appKey = secretCipher.decrypt(secret.getKisAppKey());
            String appSecret = secretCipher.decrypt(secret.getKisAppSecret());
            String accountNo = secretCipher.decrypt(secret.getKisAccountNo());
            boolean isReal = secret.isKisIsReal();
            String baseUrl = isReal ? KIS_BASE_URL_REAL : KIS_BASE_URL_SANDBOX;
            // tr_id: 실전 매수 TTTC0802U / 매도 TTTC0801U, 모의 매수 VTTC0802U / 매도 VTTC0801U
            String trId = (isReal ? "TTTC" : "VTTC") + (isBuy ? "0802U" : "0801U");
            String response = webClient.post()
                    .uri(baseUrl + ORDER_PATH)
                    .contentType(MediaType.APPLICATION_JSON)
                    .header("authorization", "Bearer " + token)
                    .header("appkey", appKey)
                    .header("appsecret", appSecret)
                    .header("tr_id", trId)
                    .bodyValue(Map.of(
                            "CANO", accountNo,
                            "ACNT_PRDT_CD", secret.getKisAccountProductCode(),
                            "PDNO", stockCode,
                            "ORD_DVSN", ordDvsn,
                            "ORD_QTY", String.valueOf(quantity),
                            "ORD_UNPR", String.valueOf(Math.max(0L, limitPrice))
                    ))
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            Map<String, Object> body = objectMapper.readValue(response, new TypeReference<>() {});
            String rtCd = String.valueOf(body.getOrDefault("rt_cd", ""));
            boolean success = "0".equals(rtCd);
            if (!success) {
                errorLogger.log("KisClient", userId, stockCode,
                        "Direct " + (isBuy ? "buy" : "sell") + " rejected by KIS: " + body.get("msg1"),
                        response);
            }
            return Map.of("success", success, "response", body);
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, stockCode,
                    "Direct " + (isBuy ? "buy" : "sell") + " failed", e.getMessage());
            return Map.of("success", false, "error", String.valueOf(e.getMessage()));
        }
    }

    /**
     * 일/주/월/년봉 조회. KIS는 한 번에 최대 100건을 반환.
     *
     * @param periodCode "D"=일봉, "W"=주봉, "M"=월봉, "Y"=년봉
     * @param fromDate inclusive (yyyyMMdd)
     * @param toDate inclusive (yyyyMMdd)
     */
    public List<CandleData> fetchDailyCandles(String userId, UserSecret secret, String token,
                                              String stockCode, String periodCode,
                                              LocalDate fromDate, LocalDate toDate) {
        try {
            String appKey = secretCipher.decrypt(secret.getKisAppKey());
            String appSecret = secretCipher.decrypt(secret.getKisAppSecret());
            boolean isReal = secret.isKisIsReal();
            String response = webClient.get()
                    .uri(uriBuilder -> uriBuilder.scheme("https")
                            .host(isReal ? "openapi.koreainvestment.com" : "openapivts.koreainvestment.com")
                            .port(isReal ? 9443 : 29443)
                            .path(CHART_DAILY_PATH)
                            .queryParam("FID_COND_MRKT_DIV_CODE", "J")
                            .queryParam("FID_INPUT_ISCD", stockCode)
                            .queryParam("FID_INPUT_DATE_1", fromDate.format(YYYYMMDD))
                            .queryParam("FID_INPUT_DATE_2", toDate.format(YYYYMMDD))
                            .queryParam("FID_PERIOD_DIV_CODE", periodCode)
                            .queryParam("FID_ORG_ADJ_PRC", "0")
                            .build())
                    .header("authorization", "Bearer " + token)
                    .header("appkey", appKey)
                    .header("appsecret", appSecret)
                    .header("tr_id", "FHKST03010100")
                    .header("custtype", "P")
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            Map<String, Object> body = objectMapper.readValue(response, new TypeReference<>() {});
            if (!"0".equals(String.valueOf(body.getOrDefault("rt_cd", "")))) {
                errorLogger.log("KisClient", userId, stockCode,
                        "daily chart rejected: " + body.get("msg1"), response);
                return List.of();
            }
            Object output2 = body.get("output2");
            if (!(output2 instanceof List<?> rows)) {
                return List.of();
            }
            List<CandleData> candles = new ArrayList<>(rows.size());
            for (Object row : rows) {
                if (!(row instanceof Map<?, ?> r)) continue;
                String date = asString(r.get("stck_bsop_date"));
                if (date == null || date.isBlank()) continue;
                long epochSec = LocalDate.parse(date, YYYYMMDD)
                        .atStartOfDay(KIS_ZONE).toEpochSecond();
                candles.add(new CandleData(
                        epochSec,
                        parseDouble(r.get("stck_oprc")),
                        parseDouble(r.get("stck_hgpr")),
                        parseDouble(r.get("stck_lwpr")),
                        parseDouble(r.get("stck_clpr")),
                        parseLong(r.get("acml_vol")),
                        Boolean.TRUE
                ));
            }
            Collections.reverse(candles); // KIS returns newest-first; we want oldest-first
            return candles;
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, stockCode, "daily chart failed", e.getMessage());
            return List.of();
        }
    }

    /**
     * 당일 분봉 조회. inquire-time-itemchartprice. 한 번에 최대 30건, 당일만 제공.
     * endHhmmss 이전의 분봉을 반환 (예: "153000"이면 09:00~15:30 사이).
     */
    public List<CandleData> fetchTodayMinuteCandles(String userId, UserSecret secret, String token,
                                                    String stockCode, String endHhmmss) {
        try {
            String appKey = secretCipher.decrypt(secret.getKisAppKey());
            String appSecret = secretCipher.decrypt(secret.getKisAppSecret());
            boolean isReal = secret.isKisIsReal();
            String response = webClient.get()
                    .uri(uriBuilder -> uriBuilder.scheme("https")
                            .host(isReal ? "openapi.koreainvestment.com" : "openapivts.koreainvestment.com")
                            .port(isReal ? 9443 : 29443)
                            .path(CHART_TODAY_MINUTE_PATH)
                            .queryParam("FID_COND_MRKT_DIV_CODE", "J")
                            .queryParam("FID_INPUT_ISCD", stockCode)
                            .queryParam("FID_INPUT_HOUR_1", endHhmmss)
                            .queryParam("FID_PW_DATA_INCU_YN", "N")
                            .queryParam("FID_ETC_CLS_CODE", "")
                            .build())
                    .header("authorization", "Bearer " + token)
                    .header("appkey", appKey)
                    .header("appsecret", appSecret)
                    .header("tr_id", "FHKST03010200")
                    .header("custtype", "P")
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            return parseMinuteOutput2(userId, stockCode, response, "today minute chart");
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, stockCode, "today minute chart failed", e.getMessage());
            return List.of();
        }
    }

    /**
     * 일별 분봉 조회. inquire-time-dailychartprice. 한 번에 최대 120건, 최대 1년치 보관.
     * (date, endHhmmss) 시각을 종료 기준으로 1분봉을 거꾸로 채워서 반환.
     */
    public List<CandleData> fetchDailyMinuteCandles(String userId, UserSecret secret, String token,
                                                    String stockCode, LocalDate date, String endHhmmss) {
        try {
            String appKey = secretCipher.decrypt(secret.getKisAppKey());
            String appSecret = secretCipher.decrypt(secret.getKisAppSecret());
            boolean isReal = secret.isKisIsReal();
            String response = webClient.get()
                    .uri(uriBuilder -> uriBuilder.scheme("https")
                            .host(isReal ? "openapi.koreainvestment.com" : "openapivts.koreainvestment.com")
                            .port(isReal ? 9443 : 29443)
                            .path(CHART_DAILY_MINUTE_PATH)
                            .queryParam("FID_COND_MRKT_DIV_CODE", "J")
                            .queryParam("FID_INPUT_ISCD", stockCode)
                            .queryParam("FID_INPUT_HOUR_1", endHhmmss)
                            .queryParam("FID_INPUT_DATE_1", date.format(YYYYMMDD))
                            .queryParam("FID_PW_DATA_INCU_YN", "N")
                            .queryParam("FID_FAKE_TICK_INCU_YN", "")
                            .build())
                    .header("authorization", "Bearer " + token)
                    .header("appkey", appKey)
                    .header("appsecret", appSecret)
                    .header("tr_id", "FHKST03010230")
                    .header("custtype", "P")
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            return parseMinuteOutput2(userId, stockCode, response, "daily minute chart");
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, stockCode, "daily minute chart failed", e.getMessage());
            return List.of();
        }
    }

    /**
     * inquire-time-itemchartprice / inquire-time-dailychartprice의 output2 파싱 공통 로직.
     * KIS는 stck_bsop_date(yyyyMMdd) + stck_cntg_hour(HHmmss)로 분봉 타임스탬프를 분리해서 준다.
     * 신->구 순으로 내려오므로 오래된 것 먼저로 뒤집어서 반환.
     */
    private List<CandleData> parseMinuteOutput2(String userId, String stockCode, String response,
                                                String label) throws Exception {
        Map<String, Object> body = objectMapper.readValue(response, new TypeReference<>() {});
        if (!"0".equals(String.valueOf(body.getOrDefault("rt_cd", "")))) {
            errorLogger.log("KisClient", userId, stockCode,
                    label + " rejected: " + body.get("msg1"), response);
            return List.of();
        }
        Object output2 = body.get("output2");
        if (!(output2 instanceof List<?> rows)) {
            return List.of();
        }
        List<CandleData> candles = new ArrayList<>(rows.size());
        for (Object row : rows) {
            if (!(row instanceof Map<?, ?> r)) continue;
            String date = asString(r.get("stck_bsop_date"));
            String hour = asString(r.get("stck_cntg_hour"));
            if (date == null || date.isBlank() || hour == null || hour.isBlank()) continue;
            // KIS는 HHmmss 또는 HHmm 자리수가 잘려나오는 경우가 있어 6자리로 패딩.
            String padded = String.format("%6s", hour).replace(' ', '0');
            long epochSec = LocalDateTime.of(
                    LocalDate.parse(date, YYYYMMDD),
                    LocalTime.parse(padded, HHMMSS)
            ).atZone(KIS_ZONE).toEpochSecond();
            // 시가/고가/저가가 0으로 들어오는 빈 분봉(거래없음)은 건너뛴다.
            double open = parseDouble(r.get("stck_oprc"));
            double high = parseDouble(r.get("stck_hgpr"));
            double low = parseDouble(r.get("stck_lwpr"));
            double close = parseDouble(r.get("stck_prpr"));
            if (open <= 0 && high <= 0 && low <= 0 && close <= 0) continue;
            candles.add(new CandleData(
                    epochSec, open, high, low, close,
                    parseLong(r.get("cntg_vol")),
                    Boolean.TRUE
            ));
        }
        Collections.reverse(candles);
        return candles;
    }

    private static String asString(Object value) {
        return value == null ? null : String.valueOf(value).trim();
    }

    private static double parseDouble(Object value) {
        String s = asString(value);
        if (s == null || s.isBlank()) return 0.0;
        try { return Double.parseDouble(s); } catch (NumberFormatException e) { return 0.0; }
    }

    private static long parseLong(Object value) {
        String s = asString(value);
        if (s == null || s.isBlank()) return 0L;
        try { return Long.parseLong(s); } catch (NumberFormatException e) {
            try { return (long) Double.parseDouble(s); } catch (NumberFormatException ignored) { return 0L; }
        }
    }

    private String fetchToken(String userId, UserSecret secret) {
        String appKey;
        String appSecret;
        try {
            appKey = secretCipher.decrypt(secret.getKisAppKey());
            appSecret = secretCipher.decrypt(secret.getKisAppSecret());
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, null, "Token fetch failed (decrypt)", e.getMessage());
            return null;
        }
        boolean isReal = secret.isKisIsReal();
        String cacheKey = appKey + ":" + (isReal ? "R" : "S");
        long now = java.time.Instant.now().getEpochSecond();

        CachedToken cached = tokenCache.get(cacheKey);
        if (cached != null && cached.isFresh(now, TOKEN_REFRESH_MARGIN.getSeconds())) {
            return cached.value();
        }

        try {
            String baseUrl = isReal ? KIS_BASE_URL_REAL : KIS_BASE_URL_SANDBOX;
            String response = webClient.post()
                    .uri(baseUrl + TOKEN_PATH)
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(Map.of(
                            "grant_type", "client_credentials",
                            "appkey", appKey,
                            "appsecret", appSecret
                    ))
                    .retrieve()
                    .onStatus(s -> s.isError(), r -> r.bodyToMono(String.class)
                            .defaultIfEmpty("")
                            .map(b -> new IllegalStateException("KIS token HTTP " + r.statusCode().value() + ": " + b)))
                    .bodyToMono(String.class)
                    .block();
            Map<String, Object> body = objectMapper.readValue(response, new TypeReference<>() {});
            String token = (String) body.get("access_token");
            if (token == null || token.isBlank()) {
                errorLogger.log("KisClient", userId, null,
                        "Token fetch succeeded but access_token was null", response);
                return null;
            }
            long expiresInSec = parseLong(body.get("expires_in"));
            if (expiresInSec <= 0) expiresInSec = 86_400L; // KIS 토큰 기본 24h
            tokenCache.put(cacheKey, new CachedToken(token, now + expiresInSec));
            return token;
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, null, "Token fetch failed", e.getMessage());
            return null;
        }
    }

}
