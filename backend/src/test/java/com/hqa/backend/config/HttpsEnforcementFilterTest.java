package com.hqa.backend.config;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;

class HttpsEnforcementFilterTest {
    private HttpsEnforcementFilter filter(String env) {
        HqaProperties properties = mock(HqaProperties.class);
        when(properties.getEnv()).thenReturn(env);
        return new HttpsEnforcementFilter(properties);
    }

    private HttpServletRequest request(String uri, String remote) {
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(request.getRequestURI()).thenReturn(uri);
        when(request.getRemoteAddr()).thenReturn(remote);
        return request;
    }

    @Test
    void internalCallsFromTheContainerNetworkSkipHttpsButPublicCallsDoNot() {
        HttpsEnforcementFilter prod = filter("prod");
        assertThat(prod.shouldNotFilter(request("/api/v1/internal/trading/signals/active", "172.18.0.5"))).isTrue();
        assertThat(prod.shouldNotFilter(request("/api/v1/internal/trading/signals", "127.0.0.1"))).isTrue();
        assertThat(prod.shouldNotFilter(request("/api/v1/internal/trading/signals", "203.0.113.7"))).isFalse();
        assertThat(prod.shouldNotFilter(request("/api/v1/stocks", "172.18.0.5"))).isFalse();
        assertThat(prod.shouldNotFilter(request("/api/v1/internal/trading/signals", "backend.example.com"))).isFalse();
        assertThat(filter("dev").shouldNotFilter(request("/api/v1/stocks", "203.0.113.7"))).isTrue();
    }
}
