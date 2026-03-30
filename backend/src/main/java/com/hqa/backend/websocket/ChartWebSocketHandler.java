package com.hqa.backend.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

@Component
public class ChartWebSocketHandler extends TextWebSocketHandler {

    private final ObjectMapper objectMapper;
    private final Map<String, WebSocketSession> sessions = new ConcurrentHashMap<>();

    public ChartWebSocketHandler(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        sessions.put(session.getId(), session);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
        JsonNode node = objectMapper.readTree(message.getPayload());
        String action = node.path("action").asText();
        String timeframe = node.path("timeframe").asText("1m");
        if ("ping".equals(action)) {
            session.sendMessage(new TextMessage("{\"type\":\"pong\"}"));
            return;
        }
        if ("subscribe".equals(action)) {
            session.sendMessage(new TextMessage(objectMapper.writeValueAsString(Map.of(
                    "type", "subscribed",
                    "stock_code", extractStockCode(session),
                    "timeframe", timeframe
            ))));
            return;
        }
        if ("unsubscribe".equals(action)) {
            session.sendMessage(new TextMessage(objectMapper.writeValueAsString(Map.of(
                    "type", "unsubscribed",
                    "timeframe", timeframe
            ))));
            return;
        }
        session.sendMessage(new TextMessage("{\"type\":\"error\",\"message\":\"Unsupported action\"}"));
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        sessions.remove(session.getId());
    }

    private String extractStockCode(WebSocketSession session) {
        String path = session.getUri() == null ? "" : session.getUri().getPath();
        int index = path.lastIndexOf('/');
        return index >= 0 ? path.substring(index + 1) : "";
    }
}
