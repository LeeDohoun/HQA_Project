package com.hqa.backend.config;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

@Component
public class ApiKeyInterceptor implements HandlerInterceptor {

    private final HqaProperties properties;

    public ApiKeyInterceptor(HqaProperties properties) {
        this.properties = properties;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        String path = request.getRequestURI();
        if (path.startsWith("/api/v1/auth")) {
            return true;
        }
        if ("local".equalsIgnoreCase(properties.getEnv()) || "dev".equalsIgnoreCase(properties.getEnv())) {
            return true;
        }
        String apiKey = request.getHeader("X-API-Key");
        if (apiKey == null || apiKey.isBlank()) {
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "API key required");
            return false;
        }
        if (!properties.getSecretKey().equals(apiKey)) {
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "Invalid API key");
            return false;
        }
        return true;
    }
}
