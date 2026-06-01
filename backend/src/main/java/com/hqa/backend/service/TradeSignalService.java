package com.hqa.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import com.hqa.backend.repository.UserRepository;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TradeSignalService {

    private static final double MAX_PRICE_DRIFT_PCT = 3.0;

    private final TradeSignalRepository signalRepository;
    private final TradeSignalExecutionRepository executionRepository;
    private final UserRepository userRepository;
    private final KisClient kisClient;
    private final ErrorLogger errorLogger;
    private final ObjectMapper objectMapper;

    public TradeSignalService(TradeSignalRepository signalRepository,
                              TradeSignalExecutionRepository executionRepository,
                              UserRepository userRepository,
                              KisClient kisClient,
                              ErrorLogger errorLogger,
                              ObjectMapper objectMapper) {
        this.signalRepository = signalRepository;
        this.executionRepository = executionRepository;
        this.userRepository = userRepository;
        this.kisClient = kisClient;
        this.errorLogger = errorLogger;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public TradeSignal saveSignal(InternalTradeSignalRequest request) {
        TradeSignal signal = new TradeSignal();
        signal.setUserId(request.userId());
        signal.setSource(request.source());
        signal.setStrategyProfile(defaultString(request.strategyProfile(), "default"));
        signal.setThemeKey(request.themeKey());
        signal.setThemeName(request.themeName());
        signal.setStockCode(request.stockCode());
        signal.setStockName(request.stockName());
        signal.setAction(request.action());
        signal.setLeaderScore(request.leaderScore());
        signal.setConfidence(request.confidence());
        signal.setRiskLevel(request.riskLevel());
        signal.setPositionSize(request.positionSize());
        signal.setSignalPrice(request.signalPrice());
        signal.setStopLoss(request.stopLoss());
        signal.setReason(request.reason());
        signal.setExpiresAt(request.expiresAt());
        signal.setStatus("PENDING");
        signal.setRawPayload(toJson(request.rawPayload()));
        return signalRepository.save(signal);
    }

    @Transactional(readOnly = true)
    public List<TradeSignal> recentForUser(String userId) {
        return signalRepository.findTop100ByUserIdOrderByCreatedAtDesc(userId);
    }

    @Transactional
    public void processPendingSignals() {
        for (TradeSignal signal : signalRepository.findTop100ByStatusOrderByCreatedAtAsc("PENDING")) {
            processOne(signal);
        }
    }

    private void processOne(TradeSignal signal) {
        if (signal.getExpiresAt() != null && signal.getExpiresAt().isBefore(OffsetDateTime.now())) {
            mark(signal, "EXPIRED", "SIGNAL_EXPIRED", null, null, null, null);
            return;
        }

        Optional<User> maybeUser = userRepository.findByUserId(signal.getUserId());
        if (maybeUser.isEmpty() || !maybeUser.get().isActive() || !maybeUser.get().isAutoTradeEnabled()) {
            mark(signal, "REJECTED", "AUTO_TRADE_DISABLED", null, null, null, null);
            return;
        }

        User user = maybeUser.get();
        UserSecret secret = user.getSecret();
        if (secret == null || isBlank(secret.getKisAppKey()) || isBlank(secret.getKisAppSecret())
                || isBlank(secret.getKisAccountNo())) {
            mark(signal, "REJECTED", "KIS_SECRET_MISSING", null, null, null, null);
            return;
        }

        String token = kisClient.fetchAccessToken(user.getUserId(), secret);
        if (token == null) {
            mark(signal, "FAILED", "KIS_TOKEN_UNAVAILABLE", null, null, null, null);
            return;
        }

        Map<String, Object> balance = kisClient.inquireBalance(user.getUserId(), secret, token);
        if (!Boolean.TRUE.equals(balance.get("success"))) {
            mark(signal, "FAILED", "KIS_BALANCE_UNAVAILABLE", null, null, null, null, toJson(balance));
            return;
        }

        Long currentPrice = kisClient.inquireCurrentPrice(user.getUserId(), secret, token, signal.getStockCode());
        if (currentPrice == null || currentPrice <= 0) {
            mark(signal, "REJECTED", "CURRENT_PRICE_UNAVAILABLE", null, null, currentPrice, null);
            return;
        }

        Double drift = priceDrift(signal.getSignalPrice(), currentPrice);
        if (drift != null && drift > MAX_PRICE_DRIFT_PCT) {
            mark(signal, "REJECTED", "PRICE_DRIFT_EXCEEDED", null, null, currentPrice, drift);
            return;
        }

        boolean buy = signal.getAction() != null && signal.getAction().contains("BUY");
        int quantity = buy ? 1 : holdingQuantity(balance, signal.getStockCode());
        if (quantity <= 0) {
            mark(signal, "REJECTED", buy ? "INVALID_ORDER_QUANTITY" : "NO_SELLABLE_HOLDING", null, currentPrice, currentPrice, drift);
            return;
        }
        if (buy && availableCash(balance) < currentPrice) {
            mark(signal, "REJECTED", "INSUFFICIENT_CASH", quantity, currentPrice, currentPrice, drift);
            return;
        }

        Map<String, Object> result;
        try {
            result = buy
                    ? kisClient.buy(user.getUserId(), secret, token, signal.getStockCode(), quantity, currentPrice)
                    : kisClient.sell(user.getUserId(), secret, token, signal.getStockCode(), quantity, currentPrice);
        } catch (Exception e) {
            errorLogger.log("TradeSignalService", user.getUserId(), signal.getStockCode(), "KIS order failed", e.getMessage());
            mark(signal, "FAILED", "KIS_ORDER_FAILED", quantity, currentPrice, currentPrice, drift);
            return;
        }

        boolean success = Boolean.TRUE.equals(result.get("success"));
        mark(
                signal,
                success ? "EXECUTED" : "FAILED",
                success ? null : "KIS_ORDER_FAILED",
                quantity,
                currentPrice,
                currentPrice,
                drift,
                toJson(result)
        );
    }

    private void mark(TradeSignal signal, String status, String reason, Integer quantity,
                      Long orderPrice, Long currentPrice, Double priceDriftPct) {
        mark(signal, status, reason, quantity, orderPrice, currentPrice, priceDriftPct, null);
    }

    private void mark(TradeSignal signal, String status, String reason, Integer quantity,
                      Long orderPrice, Long currentPrice, Double priceDriftPct, String kisResponse) {
        signal.setStatus(status);
        signal.setRejectReason(reason);
        if ("EXECUTED".equals(status)) {
            signal.setExecutedAt(OffsetDateTime.now());
        }
        signalRepository.save(signal);

        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId(signal.getId());
        execution.setUserId(signal.getUserId());
        execution.setStatus(status);
        execution.setRejectReason(reason);
        execution.setQuantity(quantity);
        execution.setOrderPrice(orderPrice);
        execution.setCurrentPrice(currentPrice);
        execution.setPriceDriftPct(priceDriftPct);
        execution.setKisResponse(kisResponse);
        executionRepository.save(execution);
    }

    private Double priceDrift(Long signalPrice, Long currentPrice) {
        if (signalPrice == null || signalPrice <= 0 || currentPrice == null || currentPrice <= 0) {
            return null;
        }
        return Math.abs((currentPrice - signalPrice) * 100.0 / signalPrice);
    }

    @SuppressWarnings("unchecked")
    private long availableCash(Map<String, Object> balance) {
        Object summary = balance.get("summary");
        if (summary instanceof Map<?, ?> map) {
            Object deposit = ((Map<String, Object>) map).get("deposit");
            if (deposit instanceof Number number) {
                return number.longValue();
            }
            try {
                return deposit == null ? 0L : Long.parseLong(String.valueOf(deposit));
            } catch (NumberFormatException ignored) {
                return 0L;
            }
        }
        return 0L;
    }

    @SuppressWarnings("unchecked")
    private int holdingQuantity(Map<String, Object> balance, String stockCode) {
        Object holdings = balance.get("holdings");
        if (!(holdings instanceof List<?> rows)) {
            return 0;
        }
        for (Object row : rows) {
            if (!(row instanceof Map<?, ?> map)) {
                continue;
            }
            Map<String, Object> holding = (Map<String, Object>) map;
            if (!stockCode.equals(String.valueOf(holding.get("stockCode")))) {
                continue;
            }
            Object quantity = holding.get("quantity");
            if (quantity instanceof Number number) {
                return Math.max(0, number.intValue());
            }
            try {
                return quantity == null ? 0 : Math.max(0, Integer.parseInt(String.valueOf(quantity)));
            } catch (NumberFormatException ignored) {
                return 0;
            }
        }
        return 0;
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value == null ? Map.of() : value);
        } catch (Exception e) {
            return "{}";
        }
    }

    private static String defaultString(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
