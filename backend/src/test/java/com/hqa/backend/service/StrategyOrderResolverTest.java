package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class StrategyOrderResolverTest {
    private final StrategyTestFixture f = new StrategyTestFixture();
    private StrategyBatch batch;
    private StrategyBatchOrder unknown;

    @BeforeEach
    void unknownSell() {
        f.properties.setOrdersEnabled(true);
        batch = f.queued(basket());
        when(f.kis.paperAuctionOrder(any(), any(), any(), eq("005930"), anyInt(), any()))
                .thenReturn(Map.of("success", false, "error", "timeout"));
        when(f.kis.paperAuctionOrder(any(), any(), any(), eq("0126Z0"), anyInt(), any())).thenReturn(accepted("0000022222"));
        f.executor.runOnce();
        unknown = f.order(batch, "005930");
        assertThat(unknown.getStatus()).isEqualTo("UNKNOWN");
    }

    private static Map<String, Object> row(String odno, String code, String side, int qty, String time) {
        Map<String, Object> row = new HashMap<>(Map.of("odno", odno, "ord_dt", "20261102", "pdno", code,
                "sll_buy_dvsn_cd", side, "ord_qty", String.valueOf(qty), "ord_gno_brno", "06010", "ord_tmd", time));
        row.putAll(Map.of("tot_ccld_qty", "0", "rmn_qty", String.valueOf(qty), "avg_prvs", "0", "cncl_yn", "N",
                "cncl_cfrm_qty", "0", "rjct_qty", "0"));
        return row;
    }

    @Test
    void unknownOrdersAreListed() {
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) f.resolver.unknownOrders().get("orders");
        assertThat(rows).hasSize(1);
        assertThat(rows.get(0)).containsEntry("orderId", unknown.getId().toString()).containsEntry("stockCode", "005930")
                .containsEntry("side", "SELL").containsEntry("quantity", 10L).containsEntry("userId", "u1")
                .containsEntry("rejectReason", "ORDER_ACCEPTANCE_UNKNOWN");
    }

    @Test
    void adoptLinksAMatchingBrokerOrderAndTheReconcilerTakesOver() {
        when(f.kis.paperOrders(eq("u1"), any(), eq("token"), eq(SESSION), eq(SESSION))).thenReturn(List.of(
                row("11111", "005930", "01", 10, "083501"), row("22222", "0126Z0", "02", 3, "083502")));
        assertThatThrownBy(() -> f.resolver.resolve(unknown.getId(), "adopt", "22222", null))
                .hasMessage("BROKER_ORDER_NOT_FOUND_FOR_THIS_ORDER"); // another stock
        Map<String, Object> result = f.resolver.resolve(unknown.getId(), "adopt", "0000011111", "KIS 앱 주문내역 확인");
        assertThat(result).containsEntry("status", "SUBMITTED").containsEntry("brokerOrderId", "11111");
        assertThat(unknown.getOrderOrg()).isEqualTo("06010");
        assertThat(unknown.getKisResponse()).contains("operatorResolution").contains("KIS 앱 주문내역 확인");
        assertThatThrownBy(() -> f.resolver.resolve(unknown.getId(), "adopt", "11111", null)).hasMessage("ORDER_NOT_UNKNOWN");

        f.clock.set("2026-11-02T09:01:00+09:00");
        Map<String, Object> filled = new HashMap<>(row("11111", "005930", "01", 10, "083501"));
        filled.putAll(Map.of("tot_ccld_qty", "10", "rmn_qty", "0", "avg_prvs", "70000"));
        Map<String, Object> other = new HashMap<>(row("22222", "0126Z0", "02", 3, "083502"));
        other.putAll(Map.of("tot_ccld_qty", "3", "rmn_qty", "0", "avg_prvs", "51000"));
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(filled, other));
        f.reconciler.runOnce();
        assertThat(unknown.getStatus()).isEqualTo("FILLED");
        assertThat(batch.getStatus()).isEqualTo("COMPLETED");
    }

    @Test
    void anOrderNumberHeldByAnotherOrderCannotBeAdopted() {
        StrategyBatchOrder buy = f.order(batch, "0126Z0");
        buy.setBrokerOrderId("11111"); // already linked to the other order
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(row("11111", "005930", "01", 10, "083501")));
        assertThatThrownBy(() -> f.resolver.resolve(unknown.getId(), "adopt", "11111", null))
                .hasMessage("BROKER_ORDER_ALREADY_ASSOCIATED");
        assertThat(unknown.getStatus()).isEqualTo("UNKNOWN");
    }

    @Test
    void notSubmittedIsRefusedWhileKisListsAPlausibleOrder() {
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(row("33333", "005930", "01", 10, "083500")));
        assertThatThrownBy(() -> f.resolver.resolve(unknown.getId(), "not-submitted", null, null))
                .hasMessage("BROKER_LISTS_POSSIBLE_ORDER:33333");
        assertThat(unknown.getStatus()).isEqualTo("UNKNOWN");

        // Only an earlier order (before submission - 1 minute) and another quantity are listed.
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(row("33333", "005930", "01", 10, "080000"),
                row("44444", "005930", "01", 9, "083500")));
        Map<String, Object> result = f.resolver.resolve(unknown.getId(), "not-submitted", null, "KIS 내역에 없음");
        assertThat(result).containsEntry("status", "SKIPPED").containsEntry("rejectReason", "OPERATOR_CONFIRMED_NOT_SUBMITTED");
        f.executor.runOnce();
        verify(f.kis, times(1)).paperAuctionOrder(any(), any(), any(), eq("005930"), anyInt(), any()); // never resent
    }

    @Test
    void invalidResolutionsAreRejected() {
        assertThatThrownBy(() -> f.resolver.resolve(unknown.getId(), "resend", null, null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> f.resolver.resolve(unknown.getId(), "adopt", null, null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> f.resolver.resolve(unknown.getId(), "not-submitted", "1", null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> f.resolver.resolve(unknown.getId(), "adopt", "12a", null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> f.resolver.resolve(f.order(batch, "0126Z0").getId(), "not-submitted", null, null))
                .hasMessage("ORDER_NOT_UNKNOWN");
        verify(f.kis, never()).paperOrders(any(), any(), any(), any(), any());
    }
}
