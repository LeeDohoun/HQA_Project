package com.hqa.backend.service;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.HealthResponse;
import java.sql.Connection;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.sql.DataSource;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.stereotype.Service;

@Service
public class HealthService {

    private final HqaProperties properties;
    private final AiServerClient aiServerClient;
    private final DataSource dataSource;
    private final RedisConnectionFactory redisConnectionFactory;

    public HealthService(HqaProperties properties, DataSource dataSource, RedisConnectionFactory redisConnectionFactory, AiServerClient aiServerClient) {
        this.properties = properties;
        this.aiServerClient = aiServerClient;
        this.dataSource = dataSource;
        this.redisConnectionFactory = redisConnectionFactory;
    }

    public HealthResponse basic() {
        boolean available = aiServerClient.isAvailable();
        return new HealthResponse(available ? "ok" : "degraded", properties.getAppVersion(), properties.getEnv(), available, OffsetDateTime.now());
    }

    public Map<String, Object> detailed() {
        Map<String, String> checks = new LinkedHashMap<>();
        checks.put("api", "ok");
        checks.put("database", canConnectDb() ? "ok" : "error");
        checks.put("redis", canConnectRedis() ? "ok" : "error");
        checks.put("ai_server", aiServerClient.isAvailable() ? "ok" : "error");
        boolean healthy = checks.values().stream().allMatch("ok"::equals);
        return Map.of(
                "status", healthy ? "ok" : "degraded",
                "checks", checks,
                "version", properties.getAppVersion(),
                "environment", properties.getEnv()
        );
    }

    private boolean canConnectDb() {
        try (Connection connection = dataSource.getConnection()) {
            return connection.isValid(2);
        } catch (Exception ignored) {
            return false;
        }
    }

    private boolean canConnectRedis() {
        try (var connection = redisConnectionFactory.getConnection()) {
            return connection.ping() != null;
        } catch (Exception ignored) {
            return false;
        }
    }
}
