package com.hqa.backend.service;

import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.AccountEquityPeakRepository;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.repository.StrategyBatchRepository;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import org.springframework.stereotype.Service;
import com.hqa.backend.config.StrategyProperties;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

/** Binds PAPER users to strategies: one strategy per user and one user per strategy. */
@Service
public class StrategyAccountService {
    public static final Set<String> STAGES = Set.of("observe", "shadow", "paper");
    public static final String STRATEGY_ID_PATTERN = "[A-Za-z0-9_.-]{1,128}";
    /** Batches that may still send or reconcile orders; the account cannot be unbound while one exists. */
    public static final List<String> LIVE_BATCH_STATUSES = List.of("QUEUED", "SUBMITTING", "SUBMITTED");
    /** Orders that are, or may be, open at the broker in any batch status (PARTIAL and HALTED included). */
    public static final List<String> OPEN_ORDER_STATUSES = List.of("PENDING", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN");
    private final StrategyAccountRepository accounts;
    private final PaperAccountSnapshotService snapshots;
    private final TradeSignalRepository signals;
    private final TradeSignalExecutionRepository executions;
    private final StrategyBatchRepository batches;
    private final StrategySessionClock sessions;
    private final StrategyBatchOrderRepository batchOrders;
    private final PaperAccountGuard accountGuard;
    private final AccountEquityPeakRepository peaks;
    private final StrategyProperties properties;
    private final TransactionTemplate transactions;

    public StrategyAccountService(StrategyAccountRepository accounts, PaperAccountSnapshotService snapshots,
            TradeSignalRepository signals, TradeSignalExecutionRepository executions, StrategyBatchRepository batches,
            StrategyBatchOrderRepository batchOrders, PaperAccountGuard accountGuard, AccountEquityPeakRepository peaks,
            StrategySessionClock sessions, StrategyProperties properties, PlatformTransactionManager transactionManager) {
        this.properties = properties;
        this.transactions = new TransactionTemplate(transactionManager);
        this.batchOrders = batchOrders;
        this.accountGuard = accountGuard;
        this.peaks = peaks;
        this.accounts = accounts;
        this.snapshots = snapshots;
        this.signals = signals;
        this.executions = executions;
        this.batches = batches;
        this.sessions = sessions;
    }

    /**
     * Binds or updates a strategy account. The first time it is enabled at stage paper, the PAPER equity
     * is recorded as the activation NAV (termination base); a strategy with a termination profile cannot
     * be enabled without it. The equity read happens before the account lock and transaction.
     */
    public Map<String, Object> bind(String userId, String strategyId, String stage, Boolean enabled) {
        if (userId == null || userId.isBlank()) throw new IllegalArgumentException("userId is required");
        if (strategyId == null || !strategyId.matches(STRATEGY_ID_PATTERN)) throw new IllegalArgumentException("INVALID_STRATEGY_ID");
        if (stage == null || !STAGES.contains(stage)) throw new IllegalArgumentException("INVALID_STAGE");
        snapshots.paperUser(userId); // PAPER KIS account with complete credentials only
        boolean activating = "paper".equals(stage) && Boolean.TRUE.equals(enabled);
        boolean terminating = properties.profile(strategyId).getTerminationDrawdownPct() != null;
        Long activationNav = null;
        if (activating && accounts.findById(userId).map(account -> account.getActivatedAt() == null).orElse(true)) {
            Map<String, Object> snapshot = snapshots.snapshot(userId);
            if (Boolean.TRUE.equals(snapshot.get("success")) && snapshot.get("equity") instanceof Number equity && equity.longValue() > 0) {
                activationNav = equity.longValue();
            } else if (terminating) {
                throw new IllegalStateException("ACTIVATION_NAV_UNAVAILABLE");
            }
        }
        Long nav = activationNav;
        return transactions.execute(status -> bindLocked(userId, strategyId, stage, enabled, activating, terminating, nav));
    }

    private Map<String, Object> bindLocked(String userId, String strategyId, String stage, Boolean enabled, boolean activating,
            boolean terminating, Long activationNav) {
        // Same account lock as PaperTradeStore.save, so no LLM plan can slip in while the account is bound.
        User user = accountGuard.lock(userId);
        Optional<StrategyAccount> byStrategy = accounts.findByStrategyId(strategyId);
        if (byStrategy.isPresent() && !byStrategy.get().getUserId().equals(userId)) {
            throw new IllegalStateException("STRATEGY_ALREADY_BOUND_TO_ANOTHER_USER");
        }
        Optional<StrategyAccount> byUser = accounts.findById(userId);
        if (byUser.isPresent() && !byUser.get().getStrategyId().equals(strategyId)) {
            throw new IllegalStateException("USER_ALREADY_BOUND_TO_ANOTHER_STRATEGY");
        }
        StrategyAccount account;
        boolean peakReset = false;
        if (byUser.isPresent()) {
            account = byUser.get();
            account.setStage(stage);
            account.setEnabled(Boolean.TRUE.equals(enabled));
            account.setUpdatedAt(sessions.now());
        } else {
            // A new strategy account must not carry LLM-managed plans or orders into the strategy book.
            if (!signals.findByUserIdAndStatusIn(userId, PaperTradeStore.ACTIVE).isEmpty()
                    || !executions.findByUserIdAndStatusIn(userId, PaperTradeStore.UNRESOLVED).isEmpty()) {
                throw new IllegalStateException("ACTIVE_LLM_PLANS_PRESENT");
            }
            account = new StrategyAccount(userId, strategyId, stage, Boolean.TRUE.equals(enabled), sessions.now());
            // The drawdown stop starts from this binding: an earlier history of the broker account does not count.
            String binding = accountGuard.binding(user);
            if (peaks.existsById(binding)) peaks.deleteById(binding);
            peakReset = true;
        }
        boolean activated = false;
        if (activating && account.getActivatedAt() == null) {
            if (activationNav != null) {
                account.activate(sessions.now(), activationNav);
                activated = true;
            } else if (terminating) {
                throw new IllegalStateException("ACTIVATION_NAV_UNAVAILABLE");
            }
        }
        Map<String, Object> view = view(accounts.save(account));
        view.put("accountBinding", accountGuard.binding(user));
        view.put("equityPeakReset", peakReset);
        view.put("activatedNow", activated);
        return view;
    }

    @Transactional
    public Map<String, Object> unbind(String userId) {
        StrategyAccount account = accounts.findById(userId).orElseThrow(() -> new IllegalArgumentException("STRATEGY_ACCOUNT_NOT_FOUND"));
        // The termination record must survive: no resume under the same strategy ID by rebinding.
        if (account.isTerminated()) throw new IllegalStateException("STRATEGY_TERMINATED_CANNOT_UNBIND");
        if (batches.existsByUserIdAndStatusIn(userId, LIVE_BATCH_STATUSES)) throw new IllegalStateException("STRATEGY_BATCH_IN_PROGRESS");
        if (batchOrders.existsOrderForUserWithStatusIn(userId, OPEN_ORDER_STATUSES)) {
            throw new IllegalStateException("STRATEGY_ORDERS_OPEN");
        }
        accounts.delete(account);
        return Map.of("userId", userId, "unbound", true);
    }

    @Transactional(readOnly = true)
    public Map<String, Object> list() {
        return Map.of("accounts", accounts.findAllByOrderByStrategyIdAsc().stream().map(account -> {
            Map<String, Object> row = view(account);
            row.put("accountBinding", bindingOf(account.getUserId()));
            return row;
        }).toList());
    }

    /** The PAPER account fingerprint (never the account number), or null when the user's secret is unusable. */
    private String bindingOf(String userId) {
        try {
            return accountGuard.binding(snapshots.paperUser(userId));
        } catch (RuntimeException ex) {
            return null;
        }
    }

    public boolean isStrategyAccount(String userId) {
        return userId != null && accounts.existsById(userId);
    }

    public Optional<StrategyAccount> find(String userId) {
        return userId == null ? Optional.empty() : accounts.findById(userId);
    }

    public static Map<String, Object> view(StrategyAccount account) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("userId", account.getUserId());
        row.put("strategyId", account.getStrategyId());
        row.put("stage", account.getStage());
        row.put("enabled", account.isEnabled());
        row.put("updatedAt", account.getUpdatedAt());
        row.put("activatedAt", account.getActivatedAt());
        row.put("activationNavKrw", account.getActivationNavKrw());
        row.put("terminatedAt", account.getTerminatedAt());
        row.put("terminationReason", account.getTerminationReason());
        return row;
    }
}
