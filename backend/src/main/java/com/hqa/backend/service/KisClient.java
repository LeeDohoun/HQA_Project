package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.entity.UserSecret;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

@Component
public class KisClient {

    private static final String KIS_BASE_URL = "https://openapi.koreainvestment.com:9443";
    private static final String TOKEN_PATH = "/oauth2/tokenP";
    private static final String ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash";

    private final WebClient webClient;
    private final ObjectMapper objectMapper;
    private final ErrorLogger errorLogger;

    public KisClient(WebClient webClient, ObjectMapper objectMapper, ErrorLogger errorLogger) {
        this.webClient = webClient;
        this.objectMapper = objectMapper;
        this.errorLogger = errorLogger;
    }

    public String fetchAccessToken(String userId, UserSecret secret) {
        return fetchToken(userId, secret);
    }

    /**
     * 직접 매수. limitPrice가 0이면 시장가 주문.
     * @return 성공 여부와 KIS 응답 본문을 담은 맵
     */
    public Map<String, Object> buy(String userId, UserSecret secret, String token,
                                   String stockCode, int quantity, long limitPrice) {
        try {
            String ordDvsn = limitPrice <= 0 ? "01" : "00"; // 01=시장가, 00=지정가
            String response = webClient.post()
                    .uri(KIS_BASE_URL + ORDER_PATH)
                    .contentType(MediaType.APPLICATION_JSON)
                    .header("authorization", "Bearer " + token)
                    .header("appkey", secret.getKisAppKey())
                    .header("appsecret", secret.getKisAppSecret())
                    .header("tr_id", "TTTC0802U")
                    .bodyValue(Map.of(
                            "CANO", secret.getKisAccountNo(),
                            "ACNT_PRDT_CD", "01",
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
                        "Direct buy rejected by KIS: " + body.get("msg1"), response);
            }
            return Map.of("success", success, "response", body);
        } catch (Exception e) {
            errorLogger.log("KisClient", userId, stockCode, "Direct buy failed", e.getMessage());
            return Map.of("success", false, "error", String.valueOf(e.getMessage()));
        }
    }

    private String fetchToken(String userId, UserSecret secret) {
        try {
            String response = webClient.post()
                    .uri(KIS_BASE_URL + TOKEN_PATH)
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(Map.of(
                            "grant_type", "client_credentials",
                            "appkey", secret.getKisAppKey(),
                            "appsecret", secret.getKisAppSecret()
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
