package com.hqa.backend.service;

import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Account-scoped history from persisted plans and broker order executions. */
@Service
@Transactional(readOnly = true)
public class HistoricalTradingSnapshotService {
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private final TradeSignalRepository signals;
    private final TradeSignalExecutionRepository executions;

    public HistoricalTradingSnapshotService(TradeSignalRepository signals, TradeSignalExecutionRepository executions) {
        this.signals = signals;
        this.executions = executions;
    }

    public Map<String, Object> aiActivity(String userId, int limit) {
        requireUserId(userId);
        List<Map<String, Object>> leaders = new ArrayList<>();
        for (TradeSignal signal : signals.findTop100ByUserIdOrderByCreatedAtDesc(userId)) {
            if (leaders.size() >= Math.max(1, Math.min(20, limit))) break;
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("rank", leaders.size() + 1);
            row.put("theme", signal.getThemeName());
            row.put("themeKey", signal.getThemeKey());
            row.put("stockName", signal.getStockName());
            row.put("stockCode", signal.getStockCode());
            row.put("action", signal.getAction());
            row.put("actionCode", signal.getAction());
            row.put("confidence", signal.getConfidence());
            row.put("score", signal.getLeaderScore());
            row.put("riskLevel", signal.getRiskLevel());
            row.put("summary", signal.getReason());
            leaders.add(row);
        }
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", leaders.isEmpty() ? "empty" : "ok");
        response.put("source", "database_trade_signals");
        response.put("bestTheme", leaders.isEmpty() ? null : leaders.get(0).get("theme"));
        response.put("themeCount", leaders.stream().map(row -> row.get("themeKey"))
                .filter(value -> value != null && !value.toString().isBlank()).distinct().count());
        response.put("leaderCount", leaders.size());
        response.put("leaders", leaders);
        return response;
    }

    public Map<String, Object> orders(String userId, String date, int limit) {
        requireUserId(userId);
        boolean allDates = date == null || date.isBlank();
        LocalDate day = allDates ? LocalDate.now(KST) : parseDate(date);
        OffsetDateTime from = day.atStartOfDay(KST).toOffsetDateTime();
        OffsetDateTime until = day.plusDays(1).atStartOfDay(KST).toOffsetDateTime();
        List<TradeSignalExecution> history = executions.historyForUser(userId, allDates, from, until,
                PageRequest.of(0, Math.max(1, Math.min(500, limit))));
        Map<String, TradeSignal> plans = signals.findAllById(history.stream()
                .map(TradeSignalExecution::getSignalId).distinct().toList()).stream()
                .collect(Collectors.toMap(TradeSignal::getId, Function.identity()));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (TradeSignalExecution execution : history) {
            TradeSignal signal = plans.get(execution.getSignalId());
            if (signal == null || !userId.equals(signal.getUserId())) {
                throw new IllegalStateException("ORDER_HISTORY_PLAN_OWNER_MISMATCH");
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", execution.getId());
            row.put("signalId", signal.getId());
            row.put("orderId", execution.getOrderId());
            row.put("timestamp", execution.getSubmittedAt() != null ? execution.getSubmittedAt() : execution.getExecutedAt());
            row.put("stockName", signal.getStockName());
            row.put("stockCode", execution.getStockCode() != null ? execution.getStockCode() : signal.getStockCode());
            row.put("side", execution.getOrderSide() == null ? null : execution.getOrderSide().toLowerCase(Locale.ROOT));
            Integer quantity = execution.getSubmittedQuantity() != null ? execution.getSubmittedQuantity() : execution.getQuantity();
            row.put("quantity", quantity);
            row.put("submittedQuantity", execution.getSubmittedQuantity());
            row.put("filledQuantity", execution.getFilledQuantity());
            row.put("price", execution.getOrderPrice());
            row.put("averageFillPrice", execution.getAverageFillPrice());
            row.put("amount", quantity == null || execution.getOrderPrice() == null ? null
                    : Math.multiplyExact((long) quantity, execution.getOrderPrice()));
            row.put("filledAmount", execution.getFilledQuantity() == null || execution.getAverageFillPrice() == null ? null
                    : Math.multiplyExact((long) execution.getFilledQuantity(), execution.getAverageFillPrice()));
            row.put("status", execution.getStatus());
            row.put("rejectReason", execution.getRejectReason());
            row.put("source", "database_order_execution");
            rows.add(row);
        }
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", "ok");
        response.put("source", "database_order_executions");
        response.put("date", allDates ? null : day.toString());
        response.put("count", rows.size());
        response.put("orders", rows);
        return response;
    }

    private static LocalDate parseDate(String date) {
        try {
            return date.matches("[0-9]{8}") ? LocalDate.parse(date, DateTimeFormatter.BASIC_ISO_DATE)
                    : LocalDate.parse(date);
        } catch (DateTimeParseException ex) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "date must be YYYY-MM-DD or YYYYMMDD", null);
        }
    }

    private static void requireUserId(String userId) {
        if (userId == null || userId.isBlank()) throw new IllegalArgumentException("User ID is required");
    }
}
