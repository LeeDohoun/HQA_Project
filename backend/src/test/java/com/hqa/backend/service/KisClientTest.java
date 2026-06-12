package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.KisVerificationResult;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.ExchangeFunction;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

class KisClientTest {

    @Test
    void verifyCredentialsReusesTokenForImmediateSaveAfterVerify() {
        AtomicInteger tokenRequests = new AtomicInteger();
        AtomicInteger balanceRequests = new AtomicInteger();
        ExchangeFunction exchange = request -> {
            String path = request.url().getPath();
            if (path.equals("/oauth2/tokenP")) {
                int count = tokenRequests.incrementAndGet();
                if (count > 1) {
                    return Mono.just(json(HttpStatus.FORBIDDEN,
                            "{\"rt_cd\":\"1\",\"msg1\":\"EGW00133\"}"));
                }
                return Mono.just(json(HttpStatus.OK,
                        "{\"access_token\":\"token-1\",\"expires_in\":86400}"));
            }
            if (path.equals("/uapi/domestic-stock/v1/trading/inquire-balance")) {
                balanceRequests.incrementAndGet();
                return Mono.just(json(HttpStatus.OK,
                        "{\"rt_cd\":\"0\",\"msg1\":\"정상처리\"}"));
            }
            return Mono.just(json(HttpStatus.NOT_FOUND, "{}"));
        };

        KisClient client = new KisClient(
                WebClient.builder().exchangeFunction(exchange).build(),
                new ObjectMapper(),
                mock(ErrorLogger.class),
                mock(SecretCipher.class)
        );

        KisVerificationResult first = client.verifyCredentials(
                "user-1", "app-key", "app-secret", "50185966", "01", false);
        KisVerificationResult second = client.verifyCredentials(
                "user-1", "app-key", "app-secret", "50185966", "01", false);

        assertThat(first.ok()).isTrue();
        assertThat(second.ok()).isTrue();
        assertThat(tokenRequests).hasValue(1);
        assertThat(balanceRequests).hasValue(2);
    }

    private static ClientResponse json(HttpStatus status, String body) {
        return ClientResponse.create(status)
                .header(HttpHeaders.CONTENT_TYPE, "application/json")
                .body(body)
                .build();
    }
}
