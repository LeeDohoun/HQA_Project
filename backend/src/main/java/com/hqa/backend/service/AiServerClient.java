package com.hqa.backend.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.HqaProperties;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

@Component
public class AiServerClient {

    private final WebClient webClient;
    private final HqaProperties properties;
    private final ObjectMapper objectMapper;

    public AiServerClient(WebClient webClient, HqaProperties properties, ObjectMapper objectMapper) {
        this.webClient = webClient;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    public void submitAnalysis(Map<String, Object> payload) {
        webClient.post()
                .uri(properties.getAiServerUrl() + "/analyze")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(payload)
                .retrieve()
                .toBodilessEntity()
                .block();
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
        String response = webClient.post()
                .uri(properties.getAiServerUrl() + "/suggest")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(payload)
                .retrieve()
                .bodyToMono(String.class)
                .block();
        return parseMap(response);
    }

    private Map<String, Object> parseMap(String body) {
        try {
            return objectMapper.readValue(body, new TypeReference<>() {});
        } catch (Exception ignored) {
            return Map.of();
        }
    }
}
