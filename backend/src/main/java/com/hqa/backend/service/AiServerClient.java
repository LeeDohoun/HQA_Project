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

    public Map<String, Object> getTradingStatus() {
        return getForMap("/trading/status");
    }

    public Map<String, Object> getTradingOrders(String date, int limit) {
        StringBuilder path = new StringBuilder("/trading/orders?limit=").append(limit);
        if (date != null && !date.isBlank()) {
            path.append("&date=").append(date);
        }
        return getForMap(path.toString());
    }

    public Map<String, Object> getStockNews(String stockCode, int limit) {
        return getForMap("/stocks/" + stockCode + "/news?limit=" + limit);
    }

    public Map<String, Object> getStockDisclosures(String stockCode, int limit) {
        return getForMap("/stocks/" + stockCode + "/disclosures?limit=" + limit);
    }

    public Map<String, Object> previewTradeDecision(Map<String, Object> payload) {
        return postForMap("/trading/decision/preview", payload, "AI 서버가 매매 미리보기를 처리하지 못했습니다");
    }

    public Map<String, Object> executeTradeDecision(Map<String, Object> payload) {
        return postForMap("/trading/decision/execute", payload, "AI 서버가 매매 실행을 처리하지 못했습니다");
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
        HttpRequest request = requestBuilder(path).GET().build();
        if (privileged(path)) {
            HttpResponse<String> response = send(request);
            ensureSuccess(path, response, "AI runtime request failed");
            return parseMap(response.body());
        }
        try {
            HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() >= 200 && response.statusCode() < 300) {
                return parseMap(response.body());
            }
            return Map.of();
        } catch (IOException | InterruptedException e) {
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            return Map.of();
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
                    "AI 서버에 연결할 수 없습니다", properties.getAiServerUrl());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503,
                    "AI 서버 요청이 중단되었습니다", e.getMessage());
        }
    }

    private void ensureSuccess(String path, HttpResponse<String> response, String failureMessage) {
        int status = response.statusCode();
        if (status >= 200 && status < 300) {
            return;
        }
        log.warn("[AiServerClient] {} failed: {} {}", path, status, response.body());
        throw new ApiException(ErrorCode.ANALYSIS_FAILED, 502,
                failureMessage, status + " " + response.body());
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
            return objectMapper.readValue(body, new TypeReference<>() {});
        } catch (Exception ignored) {
            return Map.of();
        }
    }
}
