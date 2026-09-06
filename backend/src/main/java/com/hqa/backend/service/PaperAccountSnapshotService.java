package com.hqa.backend.service;

import com.hqa.backend.entity.PaperAccountBaseline;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.PaperAccountBaselineRepository;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.UserRepository;
import java.time.Clock;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.HashSet;
import java.util.Set;
import com.hqa.backend.repository.TradeSignalRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class PaperAccountSnapshotService {
    public static final double MAX_POSITION_PCT = 20.0;
    private final UserRepository users;
    private final KisClient kis;
    private final PaperAccountBaselineRepository baselines;
    private final TradeSignalExecutionRepository executions;
    private final Clock clock;
    private final TradeSignalRepository signals;
    private final PaperTradeStore tradeStore;
    private final PaperAccountGuard accountGuard;
    private final TransactionTemplate transactions;

    @Autowired
    public PaperAccountSnapshotService(UserRepository users, KisClient kis,
            PaperAccountBaselineRepository baselines, TradeSignalExecutionRepository executions,
            TradeSignalRepository signals, PaperTradeStore tradeStore, PaperAccountGuard accountGuard,
            PlatformTransactionManager transactionManager) {
        this(users, kis, baselines, executions, signals, tradeStore, accountGuard, transactionManager, Clock.system(ZoneId.of("Asia/Seoul")));
    }

    public PaperAccountSnapshotService(UserRepository users, KisClient kis,
            PaperAccountBaselineRepository baselines, TradeSignalExecutionRepository executions,
            TradeSignalRepository signals, PaperTradeStore tradeStore, PaperAccountGuard accountGuard,
            PlatformTransactionManager transactionManager, Clock clock) {
        this.users = users;
        this.kis = kis;
        this.baselines = baselines;
        this.executions = executions;
        this.clock = clock;
        this.signals = signals;
        this.tradeStore = tradeStore;
        this.accountGuard = accountGuard;
        this.transactions = new TransactionTemplate(transactionManager);
    }

    public User paperUser(String userId) {
        User user = users.findByUserId(userId).orElseThrow(() -> new IllegalArgumentException("USER_NOT_FOUND"));
        UserSecret secret = user.getSecret();
        if (secret == null || blank(secret.getKisAppKey()) || blank(secret.getKisAppSecret())
                || blank(secret.getKisAccountNo()) || blank(secret.getKisAccountProductCode())) {
            throw new IllegalArgumentException("KIS_SECRET_MISSING");
        }
        if (secret.isKisIsReal()) throw new IllegalArgumentException("PAPER_ACCOUNT_REQUIRED");
        if (!user.isActive()) throw new IllegalArgumentException("USER_INACTIVE");
        return user;
    }

    public Map<String, Object> snapshot(String userId) {
        try {
            return transactions.execute(status -> readSnapshot(userId));
        } catch (RuntimeException ex) {
            String error = ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
            return Map.of("userId", userId, "accountMode", "PAPER", "success", false,
                    "capturedAt", OffsetDateTime.now(clock).toString(), "source", "kis", "error", error);
        }
    }

    private Map<String, Object> readSnapshot(String userId) {
        accountGuard.lock(userId);
        User user = paperUser(userId);
        String token = kis.fetchAccessToken(userId, user.getSecret());
        if (blank(token)) throw new IllegalStateException("KIS_TOKEN_UNAVAILABLE");
        Map<String, Object> balance = kis.inquireBalance(userId, user.getSecret(), token);
        if (!Boolean.TRUE.equals(balance.get("success"))) throw new IllegalStateException("KIS_BALANCE_UNAVAILABLE");
        Map<?, ?> summary = requireMap(balance.get("summary"));
        long equity = positiveLong(summary.get("netAssetAmount"));
        long cash = nonnegativeLong(summary.get("deposit"));
        OffsetDateTime now = OffsetDateTime.now(clock);
        String baselineUser = binding(user);
        String baselineId = baselineUser + ":" + now.toLocalDate();
        PaperAccountBaseline baseline = baselines.findById(baselineId).orElse(null);
        if (baseline == null) {
            Object previous = summary.get("previousDayTotalAssets");
            if (previous instanceof Number n && n.longValue() > 0) {
                baseline = baselines.save(new PaperAccountBaseline(baselineUser, now.toLocalDate(), n.longValue(), now, "kis_previous_day_assets"));
            } else if (now.toLocalTime().isBefore(LocalTime.of(9, 0))) {
                baseline = baselines.save(new PaperAccountBaseline(baselineUser, now.toLocalDate(), equity, now, "kis_preopen_equity"));
            }
        }
        Double dailyPnl = baseline == null ? null : 100.0 * (equity - baseline.getBaselineEquity()) / baseline.getBaselineEquity();
        List<Map<String, Object>> holdings = new ArrayList<>();
        if (!(balance.get("holdings") instanceof List<?> rows)) throw new IllegalStateException("INVALID_HOLDINGS");
        for (Object raw : rows) {
            Map<?, ?> row = requireMap(raw);
            Map<String, Object> holding = new LinkedHashMap<>();
            holding.put("stockCode", String.valueOf(row.get("stockCode")));
            holding.put("stockName", row.get("stockName"));
            holding.put("quantity", nonnegativeLong(row.get("quantity")));
            holding.put("sellableQuantity", nonnegativeLong(row.get("sellableQuantity")));
            holding.put("avgPrice", TradeConditions.number(row.get("avgPrice")));
            holding.put("currentPrice", positiveLong(row.get("currentPrice")));
            holding.put("evalAmount", nonnegativeLong(row.get("evalAmount")));
            holding.put("pnlRate", TradeConditions.number(row.get("evalProfitRate")));
            holdings.add(holding);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("userId", userId);
        result.put("accountMode", "PAPER");
        result.put("success", true);
        result.put("capturedAt", now.toString());
        result.put("source", "kis");
        result.put("maxPositionPct", MAX_POSITION_PCT);
        result.put("dailyBuyLimit", 20);
        result.put("orderableCash", cash);
        result.put("orderableCashSource", "deposit_upper_bound; per_order_purchasing_power_checked_at_entry");
        result.put("reservedCash", executions.reservedCashForUser(userId));
        result.put("equity", equity);
        result.put("dailyPnlPct", dailyPnl);
        result.put("dailyPnlBaselineSource", baseline == null ? null : baseline.getSource());
        result.put("entryEligible", dailyPnl != null && dailyPnl > -5.0 && user.isAutoTradeEnabled());
        result.put("entryBlockReason", dailyPnl == null ? "DAILY_BASELINE_UNAVAILABLE"
                : dailyPnl <= -5 ? "DAILY_LOSS_LIMIT_EXCEEDED" : !user.isAutoTradeEnabled() ? "AUTO_TRADE_DISABLED" : null);
        result.put("holdings", holdings);
        Set<String> symbols = new HashSet<>();
        holdings.forEach(item -> symbols.add(String.valueOf(item.get("stockCode"))));
        signals.findByUserIdAndStatusIn(userId, PaperTradeStore.ACTIVE).forEach(item -> symbols.add(item.getStockCode()));
        result.put("monitorCapacity", tradeStore.monitorCapacity());
        result.put("monitorSymbolCount", symbols.size());
        result.put("monitorCapacityExceeded", symbols.size() > tradeStore.monitorCapacity());
        return result;
    }

    public static Map<?, ?> requireMap(Object value) {
        if (!(value instanceof Map<?, ?> map)) throw new IllegalStateException("INVALID_ACCOUNT_SNAPSHOT");
        return map;
    }
    public String binding(User user) { return accountGuard.binding(user); }
    public static long positiveLong(Object value) {
        long number = nonnegativeLong(value);
        if (number <= 0) throw new IllegalStateException("POSITIVE_ACCOUNT_VALUE_REQUIRED");
        return number;
    }
    public static long nonnegativeLong(Object value) {
        double number = TradeConditions.number(value);
        if (number < 0 || number > Long.MAX_VALUE || number != Math.rint(number)) {
            throw new IllegalStateException("INVALID_ACCOUNT_VALUE");
        }
        return (long) number;
    }
    private static boolean blank(String value) { return value == null || value.isBlank(); }
}
