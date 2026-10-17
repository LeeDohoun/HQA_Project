package com.hqa.backend.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyBatchRepository;
import java.math.BigInteger;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * Operator resolution of UNKNOWN strategy orders, checked against the KIS PAPER order list of the order
 * date (same rules as the LLM path). An UNKNOWN order is never resent: "adopt" links it to a listed
 * broker order with the same stock, side and quantity that no other order holds, after which the
 * reconciler tracks it; "not-submitted" is refused while the broker lists a plausible matching order.
 */
@Service
public class StrategyOrderResolver {
    private static final DateTimeFormatter BASIC = DateTimeFormatter.BASIC_ISO_DATE;
    private final StrategyBatchOrderRepository orders;
    private final StrategyBatchRepository batches;
    private final StrategyBatchService batchService;
    private final PaperAccountSnapshotService snapshots;
    private final KisClient kis;
    private final StrategySessionClock sessions;
    private final ObjectMapper mapper;

    public StrategyOrderResolver(StrategyBatchOrderRepository orders, StrategyBatchRepository batches,
            StrategyBatchService batchService, PaperAccountSnapshotService snapshots, KisClient kis,
            StrategySessionClock sessions, ObjectMapper mapper) {
        this.orders = orders;
        this.batches = batches;
        this.batchService = batchService;
        this.snapshots = snapshots;
        this.kis = kis;
        this.sessions = sessions;
        this.mapper = mapper;
    }

    public Map<String, Object> unknownOrders() {
        List<Map<String, Object>> rows = orders.findByStatusOrderBySubmittedAtAsc("UNKNOWN").stream().map(order -> {
            StrategyBatch batch = batches.findById(order.getBatchId()).orElse(null);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("orderId", order.getId().toString());
            row.put("batchId", order.getBatchId().toString());
            row.put("strategyId", batch == null ? null : batch.getStrategyId());
            row.put("userId", batch == null ? null : batch.getUserId());
            row.put("sessionDate", batch == null ? null : batch.getSessionDate().toString());
            row.put("phase", batch == null ? null : batch.getPhase());
            row.put("seq", order.getSeq());
            row.put("stockCode", order.getStockCode());
            row.put("side", order.getSide());
            row.put("quantity", order.getQuantity());
            row.put("brokerOrderId", order.getBrokerOrderId());
            row.put("orderDate", order.getOrderDate() == null ? null : order.getOrderDate().toString());
            row.put("submittedAt", order.getSubmittedAt());
            row.put("filledQuantity", order.getFilledQuantity());
            row.put("rejectReason", order.getRejectReason());
            return row;
        }).toList();
        return Map.of("orders", rows);
    }

    public Map<String, Object> resolve(UUID orderId, String action, String brokerOrderId, String note) {
        boolean adopt = "adopt".equals(action);
        if (!adopt && !"not-submitted".equals(action)) throw new IllegalArgumentException("INVALID_RESOLUTION_ACTION");
        boolean hasId = brokerOrderId != null && !brokerOrderId.isBlank();
        if (adopt != hasId) throw new IllegalArgumentException("brokerOrderId is required for adopt and only for adopt");
        if (adopt && !brokerOrderId.trim().matches("[0-9]{1,20}")) throw new IllegalArgumentException("INVALID_BROKER_ORDER_ID");
        if (note != null && note.length() > 500) throw new IllegalArgumentException("note must be at most 500 characters");
        StrategyBatchOrder order = orders.findById(orderId).orElseThrow(() -> new IllegalArgumentException("ORDER_NOT_FOUND"));
        if (!"UNKNOWN".equals(order.getStatus())) throw new IllegalStateException("ORDER_NOT_UNKNOWN");
        StrategyBatch batch = batches.findById(order.getBatchId()).orElseThrow(() -> new IllegalStateException("BATCH_NOT_FOUND"));
        User user = snapshots.paperUser(batch.getUserId());
        if (!snapshots.binding(user).equals(batch.getAccountBinding())) throw new IllegalStateException("ACCOUNT_BINDING_CHANGED");
        LocalDate day = order.getOrderDate() != null ? order.getOrderDate()
                : order.getSubmittedAt() != null ? order.getSubmittedAt().atZoneSameInstant(StrategySessionClock.KST).toLocalDate()
                : batch.getSessionDate();
        String token = kis.fetchAccessToken(batch.getUserId(), user.getSecret());
        if (token == null || token.isBlank()) throw new IllegalStateException("KIS_TOKEN_UNAVAILABLE");
        String side = order.isBuy() ? "02" : "01";
        List<Map<String, Object>> sameOrder = kis.paperOrders(batch.getUserId(), user.getSecret(), token, day, day).stream()
                .filter(row -> day.format(BASIC).equals(String.valueOf(row.get("ord_dt")))
                        && order.getStockCode().equals(String.valueOf(row.get("pdno")))
                        && side.equals(String.valueOf(row.get("sll_buy_dvsn_cd")))
                        && quantity(row.get("ord_qty")) == order.getQuantity())
                .toList();
        OffsetDateTime now = sessions.now();
        StrategyBatchOrder current = orders.findById(orderId).orElseThrow();
        if (!"UNKNOWN".equals(current.getStatus())) throw new IllegalStateException("ORDER_NOT_UNKNOWN");
        Map<String, Object> resolution = new LinkedHashMap<>();
        resolution.put("action", action);
        resolution.put("note", note == null || note.isBlank() ? null : note.strip());
        resolution.put("at", now.toString());
        if (adopt) {
            List<Map<String, Object>> match = sameOrder.stream().filter(row -> sameOrderNumber(brokerOrderId.trim(), row.get("odno"))).toList();
            if (match.size() != 1) throw new IllegalStateException("BROKER_ORDER_NOT_FOUND_FOR_THIS_ORDER");
            String listed = String.valueOf(match.get(0).get("odno")).trim();
            if (linkedElsewhere(current, listed, day)) throw new IllegalStateException("BROKER_ORDER_ALREADY_ASSOCIATED");
            current.setStatus("SUBMITTED");
            current.setBrokerOrderId(listed);
            Object organization = match.get(0).get("ord_gno_brno");
            if (organization != null && !String.valueOf(organization).isBlank()) current.setOrderOrg(String.valueOf(organization).trim());
            current.setOrderDate(day);
            current.setRejectReason(null);
            resolution.put("brokerOrderId", listed);
        } else {
            LocalTime from = current.getSubmittedAt() == null ? null
                    : current.getSubmittedAt().atZoneSameInstant(StrategySessionClock.KST).toLocalTime().minusMinutes(1);
            List<String> possible = sameOrder.stream()
                    .filter(row -> {
                        LocalTime at = orderTime(row.get("ord_tmd"));
                        return from == null || at == null || !at.isBefore(from);
                    })
                    .map(row -> String.valueOf(row.get("odno")).trim())
                    .filter(listed -> !linkedElsewhere(current, listed, day)).toList();
            if (!possible.isEmpty()) throw new IllegalStateException("BROKER_LISTS_POSSIBLE_ORDER:" + String.join(",", possible));
            current.setStatus("SKIPPED");
            current.setRejectReason("OPERATOR_CONFIRMED_NOT_SUBMITTED");
        }
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("operatorResolution", resolution);
        record.put("previousResponse", current.getKisResponse());
        current.setKisResponse(json(record));
        current.setUpdatedAt(now);
        orders.saveAndFlush(current);
        if (!List.of("QUEUED", "SUBMITTING").contains(batch.getStatus())) {
            batchService.finalizeStatus(batch, orders.findByBatchIdOrderBySeqAsc(batch.getId()), now);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("orderId", current.getId().toString());
        result.put("batchId", batch.getId().toString());
        result.put("status", current.getStatus());
        result.put("brokerOrderId", current.getBrokerOrderId());
        result.put("rejectReason", current.getRejectReason());
        result.put("batchStatus", batch.getStatus());
        return result;
    }

    private boolean linkedElsewhere(StrategyBatchOrder order, String listed, LocalDate day) {
        return orders.findByOrderDateAndBrokerOrderIdIsNotNull(day).stream()
                .anyMatch(other -> !other.getId().equals(order.getId()) && sameOrderNumber(other.getBrokerOrderId(), listed));
    }

    private static boolean sameOrderNumber(String a, Object b) {
        if (a == null || b == null) return false;
        try {
            return new BigInteger(a.trim()).equals(new BigInteger(String.valueOf(b).trim()));
        } catch (NumberFormatException ex) {
            return a.trim().equals(String.valueOf(b).trim());
        }
    }

    private static LocalTime orderTime(Object raw) {
        try {
            return raw == null ? null : LocalTime.parse(String.valueOf(raw).trim(), DateTimeFormatter.ofPattern("HHmmss"));
        } catch (DateTimeParseException ex) {
            return null;
        }
    }

    private static long quantity(Object raw) {
        try {
            double value = Double.parseDouble(String.valueOf(raw).trim());
            return value == Math.rint(value) && value >= 0 ? (long) value : -1;
        } catch (NumberFormatException ex) {
            return -1;
        }
    }

    private String json(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            return String.valueOf(value);
        }
    }
}
