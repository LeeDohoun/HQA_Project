package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.DirectBuyRequest;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.AutoTradeService;
import com.hqa.backend.service.ErrorLogger;
import com.hqa.backend.service.HistoricalTradingSnapshotService;
import com.hqa.backend.service.KisClient;
import com.hqa.backend.service.SecretCipher;
import com.hqa.backend.service.TradeSignalService;
import jakarta.servlet.http.HttpSession;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

/** PAPER accounts only: direct orders with a REAL KIS secret never reach an order endpoint. */
class PaperOnlyDirectOrderTest {
    private final AuthService auth = mock(AuthService.class);
    private final KisClient kis = mock(KisClient.class);
    private final HttpSession session = mock(HttpSession.class);
    private final User user = new User();
    private final TradingController controller = new TradingController(mock(AiServerClient.class), mock(AutoTradeService.class),
            auth, kis, mock(TradeSignalService.class), mock(HistoricalTradingSnapshotService.class),
            mock(StrategyAccountRepository.class));

    PaperOnlyDirectOrderTest() {
        user.setUserId("u1");
        UserSecret secret = new UserSecret();
        secret.setKisAppKey("key");
        secret.setKisAppSecret("secret");
        secret.setKisAccountNo("12345678");
        secret.setKisAccountProductCode("01");
        user.setSecret(secret);
        when(auth.requireUser(session)).thenReturn(user);
    }

    private static DirectBuyRequest request() {
        DirectBuyRequest request = new DirectBuyRequest();
        request.setStockName("삼성전자");
        request.setStockCode("005930");
        request.setQuantity(1);
        request.setLimitPrice(70_000);
        return request;
    }

    @Test
    void realSecretsAreRefusedWith409BeforeAnyKisCall() {
        user.getSecret().setKisIsReal(true);
        for (Runnable call : new Runnable[] {() -> controller.directBuy(request(), session), () -> controller.directSell(request(), session)}) {
            assertThatThrownBy(call::run).isInstanceOfSatisfying(ApiException.class, ex -> {
                assertThat(ex.getStatus()).isEqualTo(409);
                assertThat(ex.getErrorCode()).isEqualTo(ErrorCode.PAPER_ACCOUNT_REQUIRED);
                assertThat(ex.getMessage()).isEqualTo("모의투자 계좌만 주문할 수 있습니다");
            });
        }
        verifyNoInteractions(kis);
    }

    @Test
    void paperSecretsStillOrder() {
        when(kis.fetchAccessToken(eq("u1"), any())).thenReturn("token");
        when(kis.buy(any(), any(), any(), any(), anyInt(), anyLong())).thenReturn(Map.of("success", true));
        assertThat(controller.directBuy(request(), session)).containsEntry("success", true).containsEntry("side", "buy");
        verify(kis).buy("u1", user.getSecret(), "token", "005930", 1, 70_000L);
    }

    @Test
    void kisClientRefusesRealSecretsForBuyAndSellBeforeHttp() {
        AtomicInteger requests = new AtomicInteger();
        SecretCipher cipher = mock(SecretCipher.class);
        when(cipher.decrypt(anyString())).thenAnswer(inv -> inv.getArgument(0));
        KisClient client = new KisClient(WebClient.builder().exchangeFunction(request -> {
            requests.incrementAndGet();
            return Mono.error(new IllegalStateException("no HTTP expected"));
        }).build(), new ObjectMapper(), mock(ErrorLogger.class), cipher);
        user.getSecret().setKisIsReal(true);
        assertThatThrownBy(() -> client.buy("u1", user.getSecret(), "token", "005930", 1, 70_000))
                .isInstanceOf(IllegalArgumentException.class).hasMessage("PAPER_ACCOUNT_REQUIRED");
        assertThatThrownBy(() -> client.sell("u1", user.getSecret(), "token", "005930", 1, 70_000))
                .isInstanceOf(IllegalArgumentException.class).hasMessage("PAPER_ACCOUNT_REQUIRED");
        assertThat(requests).hasValue(0);
    }
}
