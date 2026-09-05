package com.hqa.backend.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.beans.factory.annotation.Value;
import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import reactor.core.publisher.Mono;

@Configuration
public class WebClientConfig {

    @Bean
    public WebClient webClient(WebClient.Builder builder,
            @Value("${hqa.kis-paper-requests-per-second:1}") double paperRequestsPerSecond) {
        if (!Double.isFinite(paperRequestsPerSecond) || paperRequestsPerSecond <= 0) {
            throw new IllegalArgumentException("Invalid PAPER request rate");
        }
        long spacing = (long) Math.ceil(1_000_000_000.0 / paperRequestsPerSecond);
        ConcurrentHashMap<String, AtomicLong> slots = new ConcurrentHashMap<>();
        return builder.filter((request, next) -> {
            if (!"openapivts.koreainvestment.com".equals(request.url().getHost())
                    || request.headers().getFirst("appkey") == null) return next.exchange(request);
            String key = Integer.toHexString(request.headers().getFirst("appkey").hashCode());
            long now = System.nanoTime();
            AtomicLong slot = slots.computeIfAbsent(key, ignored -> new AtomicLong(now));
            long wait;
            while (true) {
                long previous = slot.get();
                wait = Math.max(0, previous - now);
                if (wait > Duration.ofSeconds(20).toNanos()) {
                    return Mono.error(new IllegalStateException("PAPER_RATE_QUEUE_CAPACITY_EXCEEDED"));
                }
                if (slot.compareAndSet(previous, Math.max(previous, now) + spacing)) break;
            }
            return Mono.delay(Duration.ofNanos(wait)).then(Mono.defer(() -> next.exchange(request)));
        }).build();
    }
}
