package com.hqa.backend.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class RateLimitInterceptorTest {

    @Test
    void localEnvironmentDoesNotRateLimitInteractiveDemoRequests() throws Exception {
        HqaProperties properties = new HqaProperties();
        properties.setEnv("local");
        properties.setRateLimitPerMinute(1);
        RateLimitInterceptor interceptor = new RateLimitInterceptor(properties);

        MockHttpServletRequest first = request();
        MockHttpServletResponse firstResponse = new MockHttpServletResponse();
        MockHttpServletRequest second = request();
        MockHttpServletResponse secondResponse = new MockHttpServletResponse();

        assertThat(interceptor.preHandle(first, firstResponse, new Object())).isTrue();
        assertThat(interceptor.preHandle(second, secondResponse, new Object())).isTrue();
        assertThat(secondResponse.getStatus()).isEqualTo(200);
    }

    @Test
    void productionEnvironmentStillRateLimitsRequests() throws Exception {
        HqaProperties properties = new HqaProperties();
        properties.setEnv("production");
        properties.setRateLimitPerMinute(1);
        RateLimitInterceptor interceptor = new RateLimitInterceptor(properties);

        MockHttpServletRequest first = request();
        MockHttpServletResponse firstResponse = new MockHttpServletResponse();
        MockHttpServletRequest second = request();
        MockHttpServletResponse secondResponse = new MockHttpServletResponse();

        assertThat(interceptor.preHandle(first, firstResponse, new Object())).isTrue();
        assertThat(interceptor.preHandle(second, secondResponse, new Object())).isFalse();
        assertThat(secondResponse.getStatus()).isEqualTo(429);
    }

    private static MockHttpServletRequest request() {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/stocks/search");
        request.setRemoteAddr("127.0.0.1");
        return request;
    }

    @Test
    void onlyAuthenticatedInternalRequestsBypassThePublicIpQuota() throws Exception {
        HqaProperties properties = new HqaProperties();
        properties.setEnv("production"); properties.setInternalToken("internal-test"); properties.setRateLimitPerMinute(1);
        RateLimitInterceptor interceptor = new RateLimitInterceptor(properties);
        for (int i = 0; i < 60; i++) {
            var internal = new MockHttpServletRequest("POST", "/api/v1/internal/trading/account-snapshots");
            internal.addHeader("X-HQA-Internal-Token", "internal-test");
            assertThat(interceptor.preHandle(internal, new MockHttpServletResponse(), new Object())).isTrue();
        }
        var publicRequest = request(); publicRequest.addHeader("X-HQA-Internal-Token", "internal-test");
        assertThat(interceptor.preHandle(publicRequest, new MockHttpServletResponse(), new Object())).isTrue();
        assertThat(interceptor.preHandle(publicRequest, new MockHttpServletResponse(), new Object())).isFalse();
        var invalid = new MockHttpServletRequest("POST", "/api/v1/internal/trading/account-snapshots");
        invalid.setRemoteAddr("127.0.0.1"); invalid.addHeader("X-HQA-Internal-Token", "wrong");
        assertThat(interceptor.preHandle(invalid, new MockHttpServletResponse(), new Object())).isFalse();
    }
}
