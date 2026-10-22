package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.exception.ApiException;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * HTTP client for the AI server (FastAPI/Uvicorn on port 8001).
 *
 * Uses java.net.http.HttpClient (HTTP/1.1) directly instead of WebClient.
 * Reactor Netty was sending headers that Uvicorn rejected with
 * "Unsupported upgrade request", causing POST bodies to be dropped.
 */
@Component
public class AiServerClient {

    private static final Logger log = LoggerFactory.getLogger(AiServerClient.class);
    private static final Duration TIMEOUT = Duration.ofSeconds(30);

    private final HttpClient http;
    private final HqaProperties properties;
    private final ObjectMapper objectMapper;

    public AiServerClient(HqaProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(10))
                .build();
    }

    public Map<String, Object> suggest(Map<String, Object> payload) {
        return postForMap("/suggest", payload, "AI 서버가 추천 요청을 처리하지 못했습니다");
    }

    public Map<String, Object> chat(Map<String, Object> payload) {
        return postForMap("/chat", payload, "AI 서버가 채팅 요청을 처리하지 못했습니다");
    }

    public Map<String, Object> getTradingOrders(String date, int limit) {
        StringBuilder path = new StringBuilder("/trading/orders?limit=").append(limit);
        if (date != null && !date.isBlank()) {
            path.append("&date=").append(date);
        }
        return getForMap(path.toString());
    }

    public Map<String, Object> getStockNews(String stockCode, int limit) {
        validateStockCode(stockCode);
        return getForMap("/stocks/" + stockCode + "/news?limit=" + limit);
    }

    public Map<String, Object> getStockDisclosures(String stockCode, int limit) {
        validateStockCode(stockCode);
        return getForMap("/stocks/" + stockCode + "/disclosures?limit=" + limit);
    }

    public Map<String, Object> submitMultiThemeTrade(Map<String, Object> payload) {
        return postForMap("/runtime/multi-theme-trade", payload, "AI 서버가 주도주 신호 생성을 처리하지 못했습니다");
    }

    public Map<String, Object> submitStockPreview(String stockCode) {
        return postForMap("/runtime/stock-preview", Map.of("stock_code", stockCode), "종목 분석을 시작하지 못했습니다");
    }

    public Map<String, Object> getRuntimeTask(String taskId) {
        if (taskId == null || !taskId.matches("[A-Za-z0-9_-]+")) {
            throw new IllegalArgumentException("Invalid runtime task ID");
        }
        String path = "/runtime/tasks/" + taskId;
        HttpResponse<String> response = send(requestBuilder(path).GET().build());
        if (response.statusCode() == 404) {
            throw new ApiException(ErrorCode.ANALYSIS_NOT_FOUND, 404, "AI runtime task is no longer available", null);
        }
        ensureSuccess(path, response, "AI runtime request failed");
        return parseMap(response.body());
    }


    private Map<String, Object> postForMap(String path, Object payload, String failureMessage) {
        byte[] body = serialize(payload);
        HttpResponse<String> response = send(buildPost(path, body));
        ensureSuccess(path, response, failureMessage);
        return parseMap(response.body());
    }


    private Map<String, Object> getForMap(String path) {
        HttpResponse<String> response = send(requestBuilder(path).GET().build());
        ensureSuccess(path, response, "AI 서버에서 데이터를 불러오지 못했습니다");
        Map<String, Object> result = parseMap(response.body());
        if (result.containsKey("error")) {
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503,
                    "AI 데이터 조회에 실패했습니다. 잠시 후 다시 시도해 주세요", null);
        }
        return result;
    }

    /** A bounded readiness probe; never exposes the upstream health payload. */
    public boolean isAvailable() {
        try {
            HttpResponse<String> response = send(requestBuilder("/health")
                    .timeout(Duration.ofSeconds(2)).GET().build());
            return response.statusCode() == 200 && "ok".equals(parseMap(response.body()).get("status"));
        } catch (ApiException exception) {
            return false;
        }
    }

    private static void validateStockCode(String stockCode) {
        if (stockCode == null || !stockCode.matches("[0-9]{6}")) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "6자리 종목 코드가 필요합니다", null);
        }
    }

    private HttpRequest buildPost(String path, byte[] body) {
        return requestBuilder(path)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
    }

    private HttpRequest.Builder requestBuilder(String path) {
        HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(properties.getAiServerUrl() + path))
                .timeout(TIMEOUT)
                .header("Accept", "application/json");
        if (privileged(path)) {
            String token = properties.getInternalToken();
            if (token == null || token.isBlank()) {
                throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503, "AI runtime internal token is not configured", null);
            }
            builder.header("X-HQA-Internal-Token", token);
        }
        return builder;
    }

    private static boolean privileged(String path) {
        return path.startsWith("/runtime/") || path.startsWith("/internal/runtime/")
                || path.equals("/chat") || path.equals("/suggest");
    }

    private HttpResponse<String> send(HttpRequest request) {
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503,
                    "AI 서버에 연결할 수 없습니다", null);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503,
                    "AI 서버 요청이 중단되었습니다", null);
        }
    }

    private void ensureSuccess(String path, HttpResponse<String> response, String failureMessage) {
        int status = response.statusCode();
        if (status >= 200 && status < 300) {
            return;
        }
        log.warn("[AiServerClient] {} failed with status {}", path, status);
        throw new ApiException(ErrorCode.ANALYSIS_FAILED, 502,
                failureMessage, null);
    }

    private byte[] serialize(Object payload) {
        try {
            return objectMapper.writeValueAsBytes(payload);
        } catch (Exception e) {
            throw new ApiException(ErrorCode.ANALYSIS_FAILED, 500,
                    "AI 요청 본문을 생성하지 못했습니다", e.getMessage());
        }
    }

    private Map<String, Object> parseMap(String body) {
        try {
            Map<String, Object> result = objectMapper.readValue(body, new TypeReference<>() {});
            if (result == null) throw new IOException("Expected a JSON object");
            return result;
        } catch (Exception ignored) {
            throw new ApiException(ErrorCode.ANALYSIS_FAILED, 502,
                    "AI 서버 응답 형식이 올바르지 않습니다", null);
        }
    }
}
