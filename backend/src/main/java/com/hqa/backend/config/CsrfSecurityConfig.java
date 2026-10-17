package com.hqa.backend.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.ErrorResponse;
import jakarta.servlet.Filter;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;
import org.springframework.security.web.csrf.CsrfFilter;
import org.springframework.security.web.csrf.CsrfTokenRepository;
import org.springframework.security.web.csrf.HttpSessionCsrfTokenRepository;

/** Session APIs use Spring's synchronizer token and masked request token handling. */
@Configuration(proxyBeanMethods = false)
public class CsrfSecurityConfig {
    @Bean
    public FilterRegistrationBean<Filter> apiCacheControl() {
        Filter filter = (request, response, chain) -> {
            // Session-specific content and permission flags must not be cached.
            ((HttpServletResponse) response).setHeader("Cache-Control", "no-store");
            chain.doFilter(request, response);
        };
        FilterRegistrationBean<Filter> registration = new FilterRegistrationBean<>(filter);
        registration.addUrlPatterns("/api/v1/*");
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE + 90);
        return registration;
    }

    @Bean
    public CsrfTokenRepository csrfTokenRepository() {
        HttpSessionCsrfTokenRepository repository = new HttpSessionCsrfTokenRepository();
        repository.setHeaderName("X-CSRF-Token");
        return repository;
    }

    @Bean
    public FilterRegistrationBean<CsrfFilter> csrfFilter(CsrfTokenRepository repository, ObjectMapper mapper) {
        CsrfFilter filter = new CsrfFilter(repository);
        filter.setRequireCsrfProtectionMatcher(request -> {
            String path = request.getRequestURI().substring(request.getContextPath().length());
            String servletPath = request.getServletPath()
                    + (request.getPathInfo() == null ? "" : request.getPathInfo());
            // Internal controllers authenticate service calls with X-HQA-Internal-Token.
            // Check both raw and container-normalized paths; dot/encoded path segments
            // must never turn an internal exception into a public API bypass.
            return CsrfFilter.DEFAULT_CSRF_MATCHER.matches(request)
                    && !(isInternal(path) && isInternal(servletPath));
        });
        filter.setAccessDeniedHandler((request, response, exception) -> {
            response.setStatus(403);
            response.setContentType("application/json");
            mapper.writeValue(response.getOutputStream(), ErrorResponse.of(ErrorCode.CSRF_INVALID,
                    "Security token missing or expired. Please retry.", null));
        });
        FilterRegistrationBean<CsrfFilter> registration = new FilterRegistrationBean<>(filter);
        registration.addUrlPatterns("/api/v1/*");
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE + 100);
        return registration;
    }

    private static boolean isInternal(String path) {
        return path.equals("/api/v1/internal") || path.startsWith("/api/v1/internal/");
    }
}
