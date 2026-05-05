package com.hqa.backend.scheduler;

import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.UserRepository;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.ErrorLogger;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 글로벌 자동매매가 ON일 때, 워치리스트의 종목들을 주기적으로 분석하고
 * AI 서버의 final_decision을 받아 /trading/decision/execute 로 위임 실행한다.
 *
 * AI 서버에 /recommend 엔드포인트가 없으므로 BE가 직접 오케스트레이션한다:
 *   POST /analyze -> poll GET /analyze/{taskId} -> POST /trading/decision/execute
 */
@Component
public class TradingScheduler {

    private static final Logger log = LoggerFactory.getLogger(TradingScheduler.class);

    /** task 결과를 기다리는 최대 시간 (ms) */
    private static final long POLL_TIMEOUT_MS = 5 * 60 * 1000L;
    private static final long POLL_INTERVAL_MS = 5 * 1000L;

    private final UserRepository userRepository;
    private final AiServerClient aiServerClient;
    private final ErrorLogger errorLogger;

    public TradingScheduler(UserRepository userRepository, AiServerClient aiServerClient,
                            ErrorLogger errorLogger) {
        this.userRepository = userRepository;
        this.aiServerClient = aiServerClient;
        this.errorLogger = errorLogger;
    }

    @Scheduled(fixedRate = 1_800_000)
    public void run() {
        List<User> allUsers = userRepository.findAllActiveWithSecretAndPreference();
        List<User> users = allUsers.stream().filter(User::isAutoTradeEnabled).toList();
        if (users.isEmpty()) {
            return;
        }
        log.info("[TradingScheduler] running for {} auto-trade-enabled users", users.size());

        Map<String, Object> tradingStatus;
        try {
            tradingStatus = aiServerClient.getTradingStatus();
        } catch (Exception e) {
            errorLogger.log("TradingScheduler", null, null,
                    "AI trading status fetch failed", e.getMessage());
            return;
        }

        List<Map<String, Object>> watchlist = extractWatchlist(tradingStatus);
        if (watchlist.isEmpty()) {
            log.info("[TradingScheduler] AI watchlist is empty — nothing to trade");
            return;
        }

        for (User user : users) {
            UserSecret secret = user.getSecret();
            if (secret == null || isBlank(secret.getKisAppKey()) || isBlank(secret.getKisAppSecret())
                    || isBlank(secret.getKisAccountNo())) {
                continue;
            }

            for (Map<String, Object> entry : watchlist) {
                String stockCode = String.valueOf(entry.getOrDefault("stock_code", entry.get("code")));
                String stockName = String.valueOf(entry.getOrDefault("stock_name", entry.getOrDefault("name", stockCode)));
                if (isBlank(stockCode) || "null".equals(stockCode)) continue;

                Map<String, Object> finalDecision = runAnalysis(user.getUserId(), stockName, stockCode);
                if (finalDecision == null) continue;

                String action = String.valueOf(finalDecision.getOrDefault("action_code",
                        finalDecision.getOrDefault("action", ""))).toUpperCase();
                if (!action.contains("BUY") && !action.contains("SELL")) {
                    continue;
                }

                int quantity = readQuantity(entry);
                Map<String, Object> payload = new HashMap<>();
                payload.put("stock_name", stockName);
                payload.put("stock_code", stockCode);
                payload.put("final_decision", finalDecision);
                payload.put("quantity", quantity);
                payload.put("trading_enabled_override", true);

                try {
                    aiServerClient.executeTradeDecision(payload);
                    log.info("[TradingScheduler] executed {} for {} qty={} user={}",
                            action, stockCode, quantity, user.getUserId());
                } catch (Exception e) {
                    errorLogger.log("TradingScheduler", user.getUserId(), stockCode,
                            "Trade execution failed", e.getMessage());
                }
            }
        }
    }

    private Map<String, Object> runAnalysis(String userId, String stockName, String stockCode) {
        String taskId = UUID.randomUUID().toString();
        try {
            aiServerClient.submitAnalysis(Map.of(
                    "task_id", taskId,
                    "stock_name", stockName,
                    "stock_code", stockCode,
                    "mode", "quick",
                    "max_retries", 0
            ));
        } catch (Exception e) {
            errorLogger.log("TradingScheduler", userId, stockCode, "submit analysis failed", e.getMessage());
            return null;
        }

        long started = System.currentTimeMillis();
        while (System.currentTimeMillis() - started < POLL_TIMEOUT_MS) {
            try { Thread.sleep(POLL_INTERVAL_MS); } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                return null;
            }
            Map<String, Object> data = aiServerClient.getAnalysis(taskId);
            String status = String.valueOf(data.getOrDefault("status", ""));
            if ("completed".equalsIgnoreCase(status)) {
                Object fd = data.get("final_decision");
                if (fd instanceof Map<?, ?> map) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> casted = (Map<String, Object>) map;
                    return casted;
                }
                return null;
            }
            if ("failed".equalsIgnoreCase(status)) {
                return null;
            }
        }
        log.warn("[TradingScheduler] analysis polling timed out for {}", stockCode);
        return null;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> extractWatchlist(Map<String, Object> status) {
        Object runtime = status.get("runtime");
        if (runtime instanceof Map<?, ?> runtimeMap) {
            Object wl = ((Map<String, Object>) runtimeMap).get("watchlist");
            if (wl instanceof List<?> list) {
                return (List<Map<String, Object>>) list;
            }
        }
        Object wl = status.get("watchlist");
        if (wl instanceof List<?> list) {
            return (List<Map<String, Object>>) list;
        }
        return List.of();
    }

    private int readQuantity(Map<String, Object> entry) {
        Object q = entry.getOrDefault("quantity", entry.get("qty"));
        if (q instanceof Number num) return Math.max(1, num.intValue());
        try {
            if (q != null) return Math.max(1, Integer.parseInt(String.valueOf(q)));
        } catch (NumberFormatException ignored) { }
        return 1;
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
