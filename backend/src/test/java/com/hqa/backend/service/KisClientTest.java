package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.KisVerificationResult;
import com.hqa.backend.entity.UserSecret;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
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
    void paperOrderInquiryConsumesEveryPageWithBrokerContinuation() {
        List<org.springframework.web.reactive.function.client.ClientRequest> requests = new ArrayList<>();
        ExchangeFunction exchange = request -> {
            requests.add(request);
            boolean first = requests.size() == 1;
            return Mono.just(ClientResponse.create(HttpStatus.OK).header(HttpHeaders.CONTENT_TYPE, "application/json")
                    .header("tr_cont", first ? "M" : "D")
                    .body(first ? "{\"rt_cd\":\"0\",\"ctx_area_fk100\":\"fk\",\"ctx_area_nk100\":\"nk\",\"output1\":[{\"odno\":\"1\"}]}"
                            : "{\"rt_cd\":\"0\",\"output1\":[{\"odno\":\"2\"}]}").build());
        };
        KisClient client = paperClient(exchange);
        var orders = client.paperOrders("u1", PaperTradeStoreTest.user().getSecret(), "token",
                LocalDate.of(2026, 9, 3), LocalDate.of(2026, 9, 4));
        assertThat(orders).hasSize(2);
        assertThat(requests.get(0).headers().getFirst("tr_id")).isEqualTo("VTTC0081R");
        assertThat(requests.get(1).headers().getFirst("tr_cont")).isEqualTo("N");
        assertThat(requests.get(1).url().getQuery()).contains("CTX_AREA_FK100=fk", "CTX_AREA_NK100=nk");
        assertThat(requests).allSatisfy(request -> assertThat(request.url().getHost()).isEqualTo("openapivts.koreainvestment.com"));
    }

    @Test
    void repeatingBrokerCursorFailsRatherThanReturningIncompleteOrders() {
        AtomicInteger count = new AtomicInteger();
        KisClient client = paperClient(request -> {
            count.incrementAndGet();
            return Mono.just(ClientResponse.create(HttpStatus.OK).header(HttpHeaders.CONTENT_TYPE, "application/json")
                    .header("tr_cont", "M").body("{\"rt_cd\":\"0\",\"ctx_area_fk100\":\"fk\",\"ctx_area_nk100\":\"nk\",\"output1\":[]}").build());
        });
        assertThatThrownBy(() -> client.paperOrders("u1", PaperTradeStoreTest.user().getSecret(), "token",
                LocalDate.of(2026, 9, 4), LocalDate.of(2026, 9, 4))).hasMessage("KIS_PAGINATION_INVALID");
        assertThat(count).hasValue(2);
    }

    @Test
    void paperMutationsUsePaperTransactionIdsAndRejectRealCredentialsBeforeHttp() {
        List<String> transactionIds = new ArrayList<>();
        KisClient client = paperClient(request -> {
            transactionIds.add(request.headers().getFirst("tr_id"));
            assertThat(request.url().getHost()).isEqualTo("openapivts.koreainvestment.com");
            return Mono.just(json(HttpStatus.OK, "{\"rt_cd\":\"0\",\"output\":{\"ODNO\":\"1\"}}"));
        });
        UserSecret secret = PaperTradeStoreTest.user().getSecret();
        assertThat(client.paperOrder("u1", secret, "token", "005930", 10, 100, "BUY")).containsEntry("success", true);
        assertThat(client.paperOrder("u1", secret, "token", "005930", 5, 100, "SELL")).containsEntry("success", true);
        assertThat(client.cancelPaperOrder("u1", secret, "token", "1", "org", 5)).containsEntry("success", true);
        assertThat(transactionIds).containsExactly("VTTC0012U", "VTTC0011U", "VTTC0013U");
        secret.setKisIsReal(true);
        assertThatThrownBy(() -> client.paperOrder("u1", secret, "token", "005930", 10, 100, "BUY"))
                .hasMessage("PAPER_ACCOUNT_REQUIRED");
        assertThatThrownBy(() -> client.cancelPaperOrder("u1", secret, "token", "1", "org", 5))
                .hasMessage("PAPER_ACCOUNT_REQUIRED");
        assertThat(transactionIds).hasSize(3);
    }

    private KisClient paperClient(ExchangeFunction exchange) {
        SecretCipher cipher = mock(SecretCipher.class);
        when(cipher.decrypt(anyString())).thenAnswer(inv -> inv.getArgument(0));
        return new KisClient(WebClient.builder().exchangeFunction(exchange).build(), new ObjectMapper(), mock(ErrorLogger.class), cipher);
    }

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
