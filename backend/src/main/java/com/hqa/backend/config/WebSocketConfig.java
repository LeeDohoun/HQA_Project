package com.hqa.backend.config;

import com.hqa.backend.websocket.ChartWebSocketHandler;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

@Configuration
@EnableWebSocket
public class WebSocketConfig implements WebSocketConfigurer {

    private final ChartWebSocketHandler chartWebSocketHandler;
    private final HqaProperties properties;

    public WebSocketConfig(ChartWebSocketHandler chartWebSocketHandler, HqaProperties properties) {
        this.chartWebSocketHandler = chartWebSocketHandler;
        this.properties = properties;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(chartWebSocketHandler, "/api/v1/charts/ws/*")
                .setAllowedOrigins(properties.getCorsOrigins().toArray(String[]::new));
    }
}
