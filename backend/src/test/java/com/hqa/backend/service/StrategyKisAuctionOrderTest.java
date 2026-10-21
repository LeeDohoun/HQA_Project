package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.entity.UserSecret;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.codec.HttpMessageWriter;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.mock.http.client.reactive.MockClientHttpRequest;
import org.springframework.web.reactive.function.BodyInserter;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

class StrategyKisAuctionOrderTest {

    @Test
    void auctionOrdersArePaperMarketOrdersAndRealCredentialsAreRefusedBeforeHttp() throws Exception {
        List<String> transactionIds = new ArrayList<>();
        List<Map<String, Object>> bodies = new ArrayList<>();
        ObjectMapper mapper = new ObjectMapper();
        SecretCipher cipher = mock(SecretCipher.class);
        when(cipher.decrypt(anyString())).thenAnswer(inv -> inv.getArgument(0));
        KisClient client = new KisClient(WebClient.builder().exchangeFunction(request -> {
            transactionIds.add(request.headers().getFirst("tr_id"));
            assertThat(request.url().getHost()).isEqualTo("openapivts.koreainvestment.com");
            MockClientHttpRequest http = new MockClientHttpRequest(request.method(), request.url());
            request.body().insert(http, new BodyInserter.Context() {
                @Override public List<HttpMessageWriter<?>> messageWriters() { return ExchangeStrategies.withDefaults().messageWriters(); }
                @Override public Optional<ServerHttpRequest> serverRequest() { return Optional.empty(); }
                @Override public Map<String, Object> hints() { return Map.of(); }
            }).block();
            try {
                bodies.add(mapper.readValue(http.getBodyAsString().block(), new com.fasterxml.jackson.core.type.TypeReference<>() { }));
            } catch (Exception ex) {
                throw new IllegalStateException(ex);
            }
            return Mono.just(ClientResponse.create(HttpStatus.OK).header(HttpHeaders.CONTENT_TYPE, "application/json")
                    .body("{\"rt_cd\":\"0\",\"output\":{\"ODNO\":\"1\",\"KRX_FWDG_ORD_ORGNO\":\"06010\"}}").build());
        }).build(), mapper, mock(ErrorLogger.class), cipher);
        UserSecret secret = PaperTradeStoreTest.user().getSecret();

        assertThat(client.paperAuctionOrder("u1", secret, "token", "005930", 10, "SELL")).containsEntry("success", true);
        assertThat(client.paperAuctionOrder("u1", secret, "token", "0126Z0", 3, "BUY")).containsEntry("success", true);
        assertThat(transactionIds).containsExactly("VTTC0011U", "VTTC0012U");
        assertThat(bodies.get(0)).containsEntry("ORD_DVSN", "01").containsEntry("ORD_UNPR", "0")
                .containsEntry("ORD_QTY", "10").containsEntry("PDNO", "005930");
        assertThat(bodies.get(1)).containsEntry("ORD_DVSN", "01").containsEntry("PDNO", "0126Z0");

        assertThatThrownBy(() -> client.paperAuctionOrder("u1", secret, "token", "005930", 0, "BUY"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> client.paperAuctionOrder("u1", secret, "token", "5930", 1, "BUY"))
                .isInstanceOf(IllegalArgumentException.class);
        secret.setKisIsReal(true);
        assertThatThrownBy(() -> client.paperAuctionOrder("u1", secret, "token", "005930", 1, "BUY"))
                .hasMessage("PAPER_ACCOUNT_REQUIRED");
        assertThat(transactionIds).hasSize(2);
    }

    @Test
    void balanceSummaryPassesSettlementCashThroughOnlyWhenKisReturnsIt() {
        SecretCipher cipher = mock(SecretCipher.class);
        when(cipher.decrypt(anyString())).thenAnswer(inv -> inv.getArgument(0));
        String withSettlement = "{\"rt_cd\":\"0\",\"output1\":[],\"output2\":[{\"dnca_tot_amt\":\"10000000\","
                + "\"tot_evlu_amt\":\"10000000\",\"pchs_amt_smtl_amt\":\"0\",\"evlu_pfls_smtl_amt\":\"0\","
                + "\"scts_evlu_amt\":\"0\",\"nass_amt\":\"10000000\",\"prvs_rcdl_excc_amt\":\"9500000\",\"nxdy_excc_amt\":\"9800000\"}]}";
        KisClient client = new KisClient(WebClient.builder().exchangeFunction(request -> Mono.just(ClientResponse.create(HttpStatus.OK)
                .header(HttpHeaders.CONTENT_TYPE, "application/json").body(withSettlement).build())).build(),
                new ObjectMapper(), mock(ErrorLogger.class), cipher);
        Map<?, ?> summary = (Map<?, ?>) client.inquireBalance("u1", PaperTradeStoreTest.user().getSecret(), "token").get("summary");
        assertThat(summary.get("settledCashD2")).isEqualTo(9_500_000L);
        assertThat(summary.get("cashD1")).isEqualTo(9_800_000L);
        assertThat(summary.get("deposit")).isEqualTo(10_000_000L);

        String without = withSettlement.replace(",\"prvs_rcdl_excc_amt\":\"9500000\",\"nxdy_excc_amt\":\"9800000\"", "");
        KisClient older = new KisClient(WebClient.builder().exchangeFunction(request -> Mono.just(ClientResponse.create(HttpStatus.OK)
                .header(HttpHeaders.CONTENT_TYPE, "application/json").body(without).build())).build(),
                new ObjectMapper(), mock(ErrorLogger.class), cipher);
        Map<?, ?> plain = (Map<?, ?>) older.inquireBalance("u1", PaperTradeStoreTest.user().getSecret(), "token").get("summary");
        assertThat(plain.containsKey("settledCashD2")).isFalse(); // missing stays missing, no fallback
    }
}
