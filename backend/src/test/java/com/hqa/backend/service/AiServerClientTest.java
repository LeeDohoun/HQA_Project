package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.exception.ApiException;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class AiServerClientTest {
    @Test
    void privilegedPostAndGetForwardInternalToken() throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        List<String> tokens = new ArrayList<>();
        server.createContext("/", exchange -> {
            tokens.add(exchange.getRequestHeaders().getFirst("X-HQA-Internal-Token"));
            exchange.getRequestBody().readAllBytes();
            byte[] body = "{\"task_id\":\"task1\"}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        try {
            HqaProperties properties = new HqaProperties();
            properties.setAiServerUrl("http://127.0.0.1:" + server.getAddress().getPort());
            properties.setInternalToken("test-internal-token");
            AiServerClient client = new AiServerClient(properties, new ObjectMapper());
            assertThat(client.submitMultiThemeTrade(Map.of("user_id", "u1"))).containsEntry("task_id", "task1");
            assertThat(client.getRuntimeTask("task1")).containsEntry("task_id", "task1");
            client.chat(Map.of("message", "test"));
            client.suggest(Map.of("query", "test"));
            assertThat(tokens).containsExactly("test-internal-token", "test-internal-token", "test-internal-token", "test-internal-token");
        } finally {
            server.stop(0);
        }
    }

    @Test
    void runtimeFailsBeforeNetworkWhenTokenIsMissing() {
        AiServerClient client = new AiServerClient(new HqaProperties(), new ObjectMapper());
        assertThatThrownBy(() -> client.submitMultiThemeTrade(Map.of())).isInstanceOf(ApiException.class)
                .hasMessageContaining("token is not configured");
        assertThatThrownBy(() -> client.getRuntimeTask("task1")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> client.getRuntimeTask("../trading/status")).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void malformedAndFailedResponsesAreNotEmptySuccesses() throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        var responseBody = new java.util.concurrent.atomic.AtomicReference<>("not-json");
        var responseStatus = new java.util.concurrent.atomic.AtomicInteger(200);
        server.createContext("/", exchange -> {
            byte[] body = responseBody.get().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(responseStatus.get(), body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        try {
            var properties = new HqaProperties();
            properties.setAiServerUrl("http://127.0.0.1:" + server.getAddress().getPort());
            var client = new AiServerClient(properties, new ObjectMapper());
            for (String invalid : List.of("not-json", "null", "[]")) {
                responseBody.set(invalid);
                assertThatThrownBy(() -> client.getStockNews("005930", 20))
                        .isInstanceOfSatisfying(ApiException.class, ex -> assertThat(ex.getStatus()).isEqualTo(502));
            }
            responseBody.set("{\"items\":[]}");
            assertThat(client.getStockNews("005930", 20)).containsEntry("items", List.of());
            responseBody.set("{\"error\":\"private-upstream-detail\",\"items\":[]}");
            assertThatThrownBy(() -> client.getStockNews("005930", 20))
                    .isInstanceOfSatisfying(ApiException.class, ex -> {
                        assertThat(ex.getStatus()).isEqualTo(503);
                        assertThat(ex.getDetail()).isNull();
                    });
            responseStatus.set(500);
            assertThatThrownBy(() -> client.getStockDisclosures("005930", 20))
                    .isInstanceOfSatisfying(ApiException.class, ex -> {
                        assertThat(ex.getStatus()).isEqualTo(502);
                        assertThat(ex.getDetail()).isNull();
                    });
            assertThat(client.isAvailable()).isFalse();
            responseStatus.set(200);
            responseBody.set("{\"status\":\"ok\"}");
            assertThat(client.isAvailable()).isTrue();
            assertThatThrownBy(() -> client.getStockNews("../health", 20)).isInstanceOf(ApiException.class);
        } finally {
            server.stop(0);
        }
    }
}
