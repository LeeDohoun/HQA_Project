package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.AiAnalyzeRequest;
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

    public void submitAnalysis(AiAnalyzeRequest payload) {
        postBodiless("/analyze", payload, "AI 서버가 분석 요청을 처리하지 못했습니다");
    }

    public Map<String, Object> getAnalysis(String taskId) {
        return getForMap("/analyze/" + taskId);
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

    public Map<String, Object> startPaperTradingLoop(String userId) {
        Map<String, Object> payload = Map.ofEntries(
                Map.entry("user_id", userId),
                Map.entry("candidate_limit", 5),
                Map.entry("per_theme_top_n", 3),
                Map.entry("top_n", 1),
                Map.entry("execute", true),
                Map.entry("preview", false),
                Map.entry("paper", true),
                Map.entry("dry_run", false),
                Map.entry("dry_run_override", false),
                Map.entry("trading_enabled_override", true),
                Map.entry("account_type_override", "paper"),
                Map.entry("buy_only", true),
                Map.entry("config_path", "config/watchlist.yaml"),
                Map.entry("save_report", true),
                Map.entry("trade_interval_minutes", 30),
                Map.entry("market_hours_only", true),
                Map.entry("poll_seconds", 30)
        );
        return postForMapAllowConflict(
                "/runtime/multi-theme-trade/loop/start",
                payload,
                "AI 서버가 자동매매 루프를 시작하지 못했습니다",
                "running"
        );
    }

    public Map<String, Object> stopPaperTradingLoop() {
        return postForMap(
                "/runtime/multi-theme-trade/loop/stop",
                Map.of(),
                "AI 서버가 자동매매 루프를 중지하지 못했습니다"
        );
    }

    private void postBodiless(String path, Object payload, String failureMessage) {
        byte[] body = serialize(payload);
        log.info("[AiServerClient] POST {} bytes={} payload={}", path, body.length, payload);
        HttpResponse<String> response = send(buildPost(path, body));
        ensureSuccess(path, response, failureMessage);
    }

    private Map<String, Object> postForMap(String path, Object payload, String failureMessage) {
        byte[] body = serialize(payload);
        HttpResponse<String> response = send(buildPost(path, body));
        ensureSuccess(path, response, failureMessage);
        return parseMap(response.body());
    }

    private Map<String, Object> postForMapAllowConflict(
            String path,
            Object payload,
            String failureMessage,
            String conflictStatus
    ) {
        byte[] body = serialize(payload);
        HttpResponse<String> response = send(buildPost(path, body));
        if (response.statusCode() == 409) {
            return Map.of("status", conflictStatus, "detail", response.body());
        }
        ensureSuccess(path, response, failureMessage);
        return parseMap(response.body());
    }

    private Map<String, Object> getForMap(String path) {
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(properties.getAiServerUrl() + path))
                .timeout(TIMEOUT)
                .header("Accept", "application/json")
                .GET()
                .build();
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
        return HttpRequest.newBuilder()
                .uri(URI.create(properties.getAiServerUrl() + path))
                .timeout(TIMEOUT)
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
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
