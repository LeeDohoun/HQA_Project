package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.RecommendItem;
import com.hqa.backend.entity.enums.EventType;
import com.hqa.backend.exception.ApiException;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientRequestException;
import org.springframework.web.reactive.function.client.WebClientResponseException;

@Component
public class AiServerClient {

    private static final Logger log = LoggerFactory.getLogger(AiServerClient.class);

    private final WebClient webClient;
    private final HqaProperties properties;
    private final ObjectMapper objectMapper;

    public AiServerClient(WebClient webClient, HqaProperties properties, ObjectMapper objectMapper) {
        this.webClient = webClient;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    public void submitAnalysis(Map<String, Object> payload) {
        try {
            String requestBody = objectMapper.writeValueAsString(payload);
            webClient.post()
                    .uri(properties.getAiServerUrl() + "/analyze")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(requestBody)
                    .retrieve()
                    .toBodilessEntity()
                    .block();
        } catch (WebClientRequestException exception) {
            throw new ApiException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    503,
                    "AI 서버에 연결할 수 없습니다",
                    properties.getAiServerUrl()
            );
        } catch (WebClientResponseException exception) {
            throw new ApiException(
                    ErrorCode.ANALYSIS_FAILED,
                    502,
                    "AI 서버가 분석 요청을 처리하지 못했습니다",
                    exception.getStatusCode() + " " + exception.getResponseBodyAsString()
            );
        } catch (Exception exception) {
            throw new ApiException(
                    ErrorCode.ANALYSIS_FAILED,
                    500,
                    "AI 요청 본문을 생성하지 못했습니다",
                    exception.getMessage()
            );
        }
    }

    public Map<String, Object> getAnalysis(String taskId) {
        String response = webClient.get()
                .uri(properties.getAiServerUrl() + "/analyze/" + taskId)
                .retrieve()
                .bodyToMono(String.class)
                .onErrorReturn("{}")
                .block();
        return parseMap(response);
    }

    public Map<String, Object> suggest(Map<String, Object> payload) {
        try {
            String requestBody = objectMapper.writeValueAsString(payload);
            String response = webClient.post()
                    .uri(properties.getAiServerUrl() + "/suggest")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(requestBody)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            return parseMap(response);
        } catch (WebClientRequestException exception) {
            throw new ApiException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    503,
                    "AI 서버에 연결할 수 없습니다",
                    properties.getAiServerUrl()
            );
        } catch (WebClientResponseException exception) {
            throw new ApiException(
                    ErrorCode.ANALYSIS_FAILED,
                    502,
                    "AI 서버가 추천 요청을 처리하지 못했습니다",
                    exception.getStatusCode() + " " + exception.getResponseBodyAsString()
            );
        } catch (Exception exception) {
            throw new ApiException(
                    ErrorCode.ANALYSIS_FAILED,
                    500,
                    "AI 요청 본문을 생성하지 못했습니다",
                    exception.getMessage()
            );
        }
    }

    public List<RecommendItem> recommend(EventType eventType) {
        try {
            String requestBody = objectMapper.writeValueAsString(Map.of("event_type", eventType.name()));
            String response = webClient.post()
                    .uri(properties.getAiServerUrl() + "/recommend")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(requestBody)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            return objectMapper.readValue(response, new TypeReference<>() {});
        } catch (Exception exception) {
            log.error("[AiServerClient] recommend call failed: {}", exception.getMessage());
            return List.of();
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
