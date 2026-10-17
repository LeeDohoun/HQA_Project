package com.hqa.backend.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * In non-local environments, reject plain-HTTP requests and add HSTS so
 * browsers refuse to downgrade on subsequent visits.
 *
 * Trusts X-Forwarded-Proto when set by a reverse proxy (server.forward-headers-strategy
 * should be enabled in application.yml for prod).
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class HttpsEnforcementFilter extends OncePerRequestFilter {

    private static final String HSTS_VALUE = "max-age=31536000; includeSubDomains";

    private final HqaProperties properties;

    public HttpsEnforcementFilter(HqaProperties properties) {
        this.properties = properties;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        // Skip in local/dev so http://localhost still works (as the API-key and rate-limit
        // interceptors do), and for service-to-service internal calls that arrive from a
        // loopback or private address (AI server, scheduler, monitor on the container network).
        if ("local".equalsIgnoreCase(properties.getEnv()) || "dev".equalsIgnoreCase(properties.getEnv())) return true;
        return request.getRequestURI().startsWith("/api/v1/internal/") && privateAddress(request.getRemoteAddr());
    }

    static boolean privateAddress(String address) {
        // getRemoteAddr() is an IP literal; anything else is refused so no host name is ever resolved.
        boolean ipv4 = address != null && address.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}");
        boolean ipv6 = address != null && address.contains(":") && address.matches("[0-9A-Fa-f:.]+(%[A-Za-z0-9]+)?");
        if (!ipv4 && !ipv6) return false;
        try {
            java.net.InetAddress parsed = java.net.InetAddress.getByName(address);
            return parsed.isLoopbackAddress() || parsed.isSiteLocalAddress() || parsed.isLinkLocalAddress();
        } catch (java.net.UnknownHostException ex) {
            return false;
        }
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String forwardedProto = request.getHeader("X-Forwarded-Proto");
        boolean secure = request.isSecure()
                || (forwardedProto != null && forwardedProto.equalsIgnoreCase("https"));

        if (!secure) {
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "HTTPS required");
            return;
        }

        response.setHeader("Strict-Transport-Security", HSTS_VALUE);
        chain.doFilter(request, response);
    }
}
