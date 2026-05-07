package com.hqa.backend.config;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

@Component
public class RateLimitInterceptor implements HandlerInterceptor {

    private final HqaProperties properties;
    private final Map<String, List<Long>> requests = new ConcurrentHashMap<>();

    public RateLimitInterceptor(HqaProperties properties) {
        this.properties = properties;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        String path = request.getRequestURI();
        if (path.startsWith("/health") || path.startsWith("/actuator")) {
            return true;
        }

        long now = Instant.now().getEpochSecond();
        String clientIp = request.getHeader("X-Forwarded-For");
        if (clientIp == null || clientIp.isBlank()) {
            clientIp = request.getRemoteAddr();
        } else {
            clientIp = clientIp.split(",")[0].trim();
        }

        List<Long> history = requests.computeIfAbsent(clientIp, key -> new ArrayList<>());
        synchronized (history) {
            history.removeIf(time -> now - time >= 60);
            if (history.size() >= properties.getRateLimitPerMinute()) {
                response.setHeader("Retry-After", "60");
                response.sendError(429, "Rate limit exceeded");
                return false;
            }
            history.add(now);
            int remaining = Math.max(0, properties.getRateLimitPerMinute() - history.size());
            response.setHeader("X-RateLimit-Limit", String.valueOf(properties.getRateLimitPerMinute()));
            response.setHeader("X-RateLimit-Remaining", String.valueOf(remaining));
        }
        return true;
    }
}
