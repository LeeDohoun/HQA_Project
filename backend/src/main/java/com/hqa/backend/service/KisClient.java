package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.KisVerificationResult;
import com.hqa.backend.entity.UserSecret;
import java.util.Map;
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

    private final WebClient webClient;
    private final ObjectMapper objectMapper;
    private final ErrorLogger errorLogger;
    private final SecretCipher secretCipher;

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

    private String fetchToken(String userId, UserSecret secret) {
        try {
            String appKey = secretCipher.decrypt(secret.getKisAppKey());
            String appSecret = secretCipher.decrypt(secret.getKisAppSecret());
            String baseUrl = secret.isKisIsReal() ? KIS_BASE_URL_REAL : KIS_BASE_URL_SANDBOX;
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
            String token = (String) body.get("access_token");
            if (token == null) {
                errorLogger.log("KisClient", userId, null,
                        "Token fetch succeeded but access_token was null", response);
            }
            return token;
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, null, "Token fetch failed", e.getMessage());
            return null;
        }
    }

}
