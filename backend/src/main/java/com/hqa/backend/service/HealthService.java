package com.hqa.backend.service;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.HealthResponse;
import java.net.URI;
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
    private final DataSource dataSource;
    private final RedisConnectionFactory redisConnectionFactory;

    public HealthService(HqaProperties properties, DataSource dataSource, RedisConnectionFactory redisConnectionFactory) {
        this.properties = properties;
        this.dataSource = dataSource;
        this.redisConnectionFactory = redisConnectionFactory;
    }

    public HealthResponse basic() {
        return new HealthResponse("ok", properties.getAppVersion(), properties.getEnv(), true, OffsetDateTime.now());
    }

    public Map<String, Object> detailed() {
        Map<String, String> checks = new LinkedHashMap<>();
        checks.put("api", "ok");
        checks.put("database", canConnectDb() ? "ok" : "error");
        checks.put("redis", canConnectRedis() ? "ok" : "error");
        checks.put("vector_store", "unknown");
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
        try {
            return redisConnectionFactory.getConnection().ping() != null;
        } catch (Exception ignored) {
            return false;
        }
    }
}
