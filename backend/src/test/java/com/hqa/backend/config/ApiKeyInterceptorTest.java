package com.hqa.backend.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class ApiKeyInterceptorTest {

    @Test
    void productionUserApiDoesNotRequireGlobalApiKey() throws Exception {
        HqaProperties properties = new HqaProperties();
        properties.setEnv("production");
        properties.setSecretKey("server-secret");
        ApiKeyInterceptor interceptor = new ApiKeyInterceptor(properties);
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/stocks/search");
        MockHttpServletResponse response = new MockHttpServletResponse();

        boolean allowed = interceptor.preHandle(request, response, new Object());

        assertThat(allowed).isTrue();
        assertThat(response.getStatus()).isEqualTo(200);
    }

    @Test
    void productionAdminApiRequiresGlobalApiKey() throws Exception {
        HqaProperties properties = new HqaProperties();
        properties.setEnv("production");
        properties.setSecretKey("server-secret");
        ApiKeyInterceptor interceptor = new ApiKeyInterceptor(properties);
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/admin/tasks");
        MockHttpServletResponse response = new MockHttpServletResponse();

        boolean allowed = interceptor.preHandle(request, response, new Object());

        assertThat(allowed).isFalse();
        assertThat(response.getStatus()).isEqualTo(401);
    }
}
