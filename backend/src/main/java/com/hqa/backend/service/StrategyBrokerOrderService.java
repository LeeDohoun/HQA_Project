package com.hqa.backend.service;

import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyBatchRepository;
import java.math.BigInteger;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;

/**
 * Read-only view of a strategy account's KIS PAPER order list for one day (VTTC0081R), with each row
 * linked to our own strategy order by broker order number. Rows without a link are foreign (placed by
 * something else on the account). Nothing is written, sent or cancelled.
 */
@Service
public class StrategyBrokerOrderService {
    private final StrategyAccountRepository accounts;
    private final StrategyBatchOrderRepository orders;
    private final StrategyBatchRepository batches;
    private final PaperAccountSnapshotService snapshots;
    private final KisClient kis;
    private final StrategySessionClock sessions;

    public StrategyBrokerOrderService(StrategyAccountRepository accounts, StrategyBatchOrderRepository orders,
            StrategyBatchRepository batches, PaperAccountSnapshotService snapshots, KisClient kis, StrategySessionClock sessions) {
        this.accounts = accounts;
        this.orders = orders;
        this.batches = batches;
        this.snapshots = snapshots;
        this.kis = kis;
        this.sessions = sessions;
    }

    public Map<String, Object> brokerOrders(String userId, LocalDate requested) {
        if (userId == null || !accounts.existsById(userId)) throw new IllegalArgumentException("STRATEGY_ACCOUNT_NOT_FOUND");
        LocalDate day = requested == null ? sessions.today() : requested;
        if (day.isAfter(sessions.today())) throw new IllegalArgumentException("DATE_IN_FUTURE");
        User user = snapshots.paperUser(userId);
        String token = kis.fetchAccessToken(userId, user.getSecret());
        if (token == null || token.isBlank()) throw new IllegalStateException("KIS_TOKEN_UNAVAILABLE");
        List<StrategyBatchOrder> ours = orders.findByOrderDateAndBrokerOrderIdIsNotNull(day).stream()
                .filter(order -> batches.findById(order.getBatchId()).map(StrategyBatch::getUserId).map(userId::equals).orElse(false))
                .toList();
        String basicDay = day.format(DateTimeFormatter.BASIC_ISO_DATE);
        List<Map<String, Object>> rows = new ArrayList<>();
        int open = 0;
        for (Map<String, Object> raw : kis.paperOrders(userId, user.getSecret(), token, day, day)) {
            if (raw.get("ord_dt") != null && !basicDay.equals(String.valueOf(raw.get("ord_dt")))) continue;
            String odno = text(raw.get("odno"));
            Long remaining = number(raw.get("rmn_qty"));
            boolean cancelled = "Y".equals(text(raw.get("cncl_yn")));
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("odno", odno);
            row.put("stockCode", text(raw.get("pdno")));
            String side = text(raw.get("sll_buy_dvsn_cd"));
            row.put("side", "02".equals(side) ? "BUY" : "01".equals(side) ? "SELL" : side);
            row.put("orderQty", number(raw.get("ord_qty")));
            row.put("filledQty", number(raw.get("tot_ccld_qty")));
            row.put("remainingQty", remaining);
            row.put("cancelled", cancelled);
            row.put("orderTime", time(raw.get("ord_tmd")));
            row.put("strategyBatchOrderId", ours.stream().filter(order -> sameOrderNumber(order.getBrokerOrderId(), odno))
                    .map(order -> order.getId().toString()).findFirst().orElse(null));
            if (remaining != null && remaining > 0 && !cancelled) open++;
            rows.add(row);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("date", day.toString());
        result.put("orders", rows);
        result.put("openOrders", open);
        return result;
    }

    private static boolean sameOrderNumber(String stored, String listed) {
        if (stored == null || listed == null || listed.isBlank()) return false;
        try {
            return new BigInteger(stored.trim()).equals(new BigInteger(listed.trim()));
        } catch (NumberFormatException ex) {
            return stored.trim().equals(listed.trim());
        }
    }

    private static String text(Object value) {
        return value == null ? null : String.valueOf(value).trim();
    }

    private static Long number(Object value) {
        try {
            double parsed = Double.parseDouble(String.valueOf(value).trim());
            return Double.isFinite(parsed) && parsed == Math.rint(parsed) ? (long) parsed : null;
        } catch (NumberFormatException | NullPointerException ex) {
            return null;
        }
    }

    private static String time(Object value) {
        try {
            return value == null ? null : LocalTime.parse(String.valueOf(value).trim(), DateTimeFormatter.ofPattern("HHmmss")).toString();
        } catch (DateTimeParseException ex) {
            return text(value);
        }
    }
}
