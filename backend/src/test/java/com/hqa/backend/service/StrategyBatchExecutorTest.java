package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.InOrder;

class StrategyBatchExecutorTest {
    private final StrategyTestFixture f = new StrategyTestFixture();

    @BeforeEach
    void enableOrders() {
        f.properties.setOrdersEnabled(true);
    }

    private void acceptAll() {
        when(f.kis.paperAuctionOrder(eq("u1"), any(), eq("token"), anyString(), anyInt(), anyString()))
                .thenAnswer(inv -> accepted("000" + inv.getArgument(3)));
    }

    @Test
    void ordersDisabledSendsNothingAndTheBatchStaysQueued() {
        StrategyBatch batch = f.queued(basket());
        f.properties.setOrdersEnabled(false);
        f.executor.tick();
        f.executor.runOnce();
        assertThat(batch.getStatus()).isEqualTo("QUEUED");
        assertThat(f.ordersOf(batch)).extracting(StrategyBatchOrder::getStatus).containsOnly("PENDING");
        verifyNoInteractions(f.kis);
    }

    @Test
    void sellsAreSentBeforeBuysAsPaperMarketOrdersAndTheBatchBecomesSubmitted() {
        StrategyBatch batch = f.queued(basket());
        // Even a stored BUY with a lower seq waits for every SELL.
        StrategyBatchOrder buy = f.order(batch, "0126Z0");
        buy.setSeq(0);
        acceptAll();
        f.executor.runOnce();
        InOrder order = inOrder(f.kis);
        order.verify(f.kis).paperAuctionOrder("u1", f.user.getSecret(), "token", "005930", 10, "SELL");
        order.verify(f.kis).paperAuctionOrder("u1", f.user.getSecret(), "token", "0126Z0", 3, "BUY");
        assertThat(batch.getStatus()).isEqualTo("SUBMITTED");
        assertThat(batch.getSubmittedAt()).isNotNull();
        StrategyBatchOrder sell = f.order(batch, "005930");
        assertThat(sell.getStatus()).isEqualTo("SUBMITTED");
        assertThat(sell.getBrokerOrderId()).isEqualTo("000005930");
        assertThat(sell.getOrderOrg()).isEqualTo("06010");
        assertThat(sell.getOrderDate()).isEqualTo(SESSION);
        assertThat(sell.getFilledQuantity()).isZero(); // acceptance is not a fill
        assertThat(sell.getRejectReason()).isNull();
    }

    @Test
    void nothingIsSentBeforeTheWindowAndUnsentOrdersAreSkippedAfterIt() {
        StrategyBatch batch = f.queued(basket());
        f.clock.set("2026-11-02T08:20:00+09:00");
        f.executor.runOnce();
        assertThat(batch.getStatus()).isEqualTo("QUEUED");
        f.clock.set("2026-11-02T08:59:00+09:00");
        f.executor.runOnce();
        verifyNoInteractions(f.kis);
        assertThat(batch.getStatus()).isEqualTo("SUBMITTED");
        assertThat(f.ordersOf(batch)).allSatisfy(order -> {
            assertThat(order.getStatus()).isEqualTo("SKIPPED");
            assertThat(order.getRejectReason()).isEqualTo("PHASE_WINDOW_CLOSED");
        });
    }

    @Test
    void haltStopsSendingInTheMiddleOfABatch() {
        StrategyBatch batch = f.queued(List.of(sell(1, "005930", 10, 70_000), sell(2, "000660", 5, 100_000),
                buy(3, "0126Z0", 3, 51_000)));
        when(f.kis.paperAuctionOrder(eq("u1"), any(), eq("token"), anyString(), anyInt(), anyString())).thenAnswer(inv -> {
            f.halt("GLOBAL"); // switched on while the first order is in flight
            return accepted("1");
        });
        f.executor.runOnce();
        verify(f.kis, times(1)).paperAuctionOrder(any(), any(), any(), any(), anyInt(), any());
        assertThat(batch.getStatus()).isEqualTo("HALTED");
        assertThat(f.order(batch, "005930").getStatus()).isEqualTo("SUBMITTED");
        assertThat(f.order(batch, "000660").getStatus()).isEqualTo("SKIPPED");
        assertThat(f.order(batch, "000660").getRejectReason()).isEqualTo("TRADING_HALTED");
        assertThat(f.order(batch, "0126Z0").getRejectReason()).isEqualTo("TRADING_HALTED");
        f.executor.runOnce();
        verify(f.kis, times(1)).paperAuctionOrder(any(), any(), any(), any(), anyInt(), any());
    }

    @Test
    void buyBlockSkipsBuysButStillSendsSells() {
        StrategyBatch batch = f.queued(List.of(sell(1, "005930", 10, 70_000), buy(2, "0126Z0", 3, 51_000),
                buy(3, "000660", 1, 100_000)));
        f.snapshot.put("dailyPnlPct", -2.5); // loss limit crossed after acceptance
        acceptAll();
        f.executor.runOnce();
        verify(f.kis).paperAuctionOrder(any(), any(), any(), eq("005930"), eq(10), eq("SELL"));
        verify(f.kis, never()).paperAuctionOrder(any(), any(), any(), any(), anyInt(), eq("BUY"));
        assertThat(f.order(batch, "005930").getStatus()).isEqualTo("SUBMITTED");
        assertThat(f.order(batch, "0126Z0").getStatus()).isEqualTo("SKIPPED");
        assertThat(f.order(batch, "0126Z0").getRejectReason()).isEqualTo("DAILY_LOSS_LIMIT_EXCEEDED");
        assertThat(f.order(batch, "000660").getRejectReason()).isEqualTo("DAILY_LOSS_LIMIT_EXCEEDED");
        assertThat(batch.getStatus()).isEqualTo("SUBMITTED");
        verify(f.snapshots, times(2)).snapshot("u1"); // once at acceptance, once (cached) for the buys
    }

    @Test
    void ambiguousOrFailedBrokerCallsBecomeUnknownAndAreNeverResent() {
        StrategyBatch batch = f.queued(List.of(sell(1, "005930", 10, 70_000), sell(2, "000660", 5, 100_000),
                buy(3, "0126Z0", 3, 51_000)));
        when(f.kis.paperAuctionOrder(any(), any(), any(), eq("005930"), anyInt(), any()))
                .thenReturn(Map.of("success", false, "error", "HTTP 502"));
        when(f.kis.paperAuctionOrder(any(), any(), any(), eq("000660"), anyInt(), any()))
                .thenThrow(new IllegalStateException("socket closed"));
        when(f.kis.paperAuctionOrder(any(), any(), any(), eq("0126Z0"), anyInt(), any()))
                .thenReturn(Map.of("success", false, "response", Map.of("rt_cd", "1", "msg_cd", "APBK0013", "msg1", "주문 거부")));
        f.executor.runOnce();
        assertThat(f.order(batch, "005930").getStatus()).isEqualTo("UNKNOWN");
        assertThat(f.order(batch, "005930").getRejectReason()).isEqualTo("ORDER_ACCEPTANCE_UNKNOWN");
        assertThat(f.order(batch, "000660").getStatus()).isEqualTo("UNKNOWN");
        assertThat(f.order(batch, "0126Z0").getStatus()).isEqualTo("REJECTED");
        assertThat(f.order(batch, "0126Z0").getRejectReason()).isEqualTo("KIS_ORDER_REJECTED");
        assertThat(batch.getStatus()).isEqualTo("SUBMITTED");
        f.executor.runOnce();
        verify(f.kis, times(3)).paperAuctionOrder(any(), any(), any(), any(), anyInt(), any());
    }

    @Test
    void anOrderInFlightIsRecordedAsUnknownBeforeTheBrokerCall() {
        StrategyBatch batch = f.queued(List.of(sell(1, "005930", 10, 70_000)));
        when(f.kis.paperAuctionOrder(any(), any(), any(), any(), anyInt(), any())).thenAnswer(inv -> {
            StrategyBatchOrder inFlight = f.order(batch, "005930");
            assertThat(inFlight.getStatus()).isEqualTo("UNKNOWN");
            assertThat(inFlight.getSubmittedAt()).isNotNull();
            return accepted("7");
        });
        f.executor.runOnce();
        assertThat(f.order(batch, "005930").getStatus()).isEqualTo("SUBMITTED");
    }

    @Test
    void ordersTheBrokerDidNotTakeAreRetriedOnTheNextTick() {
        StrategyBatch batch = f.queued(List.of(sell(1, "005930", 10, 70_000)));
        when(f.kis.paperAuctionOrder(any(), any(), any(), any(), anyInt(), any()))
                .thenReturn(Map.of("success", false, "response", Map.of("rt_cd", "1", "msg_cd", "EGW00201")))
                .thenReturn(Map.of("success", false, "notSent", true, "error", KisClient.PAPER_RATE_QUEUE_FULL))
                .thenReturn(accepted("9"));
        f.executor.runOnce();
        assertThat(f.order(batch, "005930").getStatus()).isEqualTo("PENDING");
        assertThat(f.order(batch, "005930").getRejectReason()).isEqualTo("KIS_RATE_LIMITED");
        assertThat(batch.getStatus()).isEqualTo("SUBMITTING");
        f.executor.runOnce();
        assertThat(f.order(batch, "005930").getRejectReason()).isEqualTo("ORDER_NOT_SENT_RATE_QUEUE_FULL");
        f.executor.runOnce();
        assertThat(f.order(batch, "005930").getStatus()).isEqualTo("SUBMITTED");
        assertThat(batch.getStatus()).isEqualTo("SUBMITTED");
    }

    @Test
    void aDisabledOrRebindedAccountSkipsTheBatch() {
        StrategyBatch batch = f.queued(basket());
        f.accountRows.get("u1").setEnabled(false);
        f.executor.runOnce();
        verifyNoInteractions(f.kis);
        assertThat(f.ordersOf(batch)).extracting(StrategyBatchOrder::getRejectReason).containsOnly("STRATEGY_ACCOUNT_DISABLED");

        StrategyTestFixture g = new StrategyTestFixture();
        g.properties.setOrdersEnabled(true);
        StrategyBatch other = g.queued(basket());
        when(g.snapshots.binding(g.user)).thenReturn("binding-2");
        g.executor.runOnce();
        verifyNoInteractions(g.kis);
        assertThat(g.ordersOf(other)).extracting(StrategyBatchOrder::getRejectReason).containsOnly("ACCOUNT_BINDING_CHANGED");
    }
}
