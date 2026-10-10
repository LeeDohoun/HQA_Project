package com.hqa.backend.service;

import com.hqa.backend.config.HqaProperties;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.connection.RedisConnection;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class HealthServiceTest {
    @Test
    void readinessReflectsAiAvailability() {
        var ai = mock(AiServerClient.class);
        var service = new HealthService(new HqaProperties(), mock(DataSource.class),
                mock(RedisConnectionFactory.class), ai);
        when(ai.isAvailable()).thenReturn(false, true);
        assertThat(service.basic().langgraphAvailable()).isFalse();
        assertThat(service.basic().status()).isEqualTo("ok");
    }

    @Test
    void detailedHealthClosesRedisConnectionAndIncludesAi() throws Exception {
        var ai = mock(AiServerClient.class);
        var factory = mock(RedisConnectionFactory.class);
        var redis = mock(RedisConnection.class);
        when(factory.getConnection()).thenReturn(redis);
        when(redis.ping()).thenReturn("PONG");
        var source = mock(DataSource.class);
        var connection = mock(java.sql.Connection.class);
        when(source.getConnection()).thenReturn(connection);
        when(connection.isValid(2)).thenReturn(true);
        when(ai.isAvailable()).thenReturn(true);
        var service = new HealthService(new HqaProperties(), source, factory, ai);
        assertThat(service.detailed()).containsEntry("status", "ok");
        verify(redis).close();
        verify(connection).close();
    }
}
