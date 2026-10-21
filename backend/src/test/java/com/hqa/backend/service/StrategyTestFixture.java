package com.hqa.backend.service;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.hqa.backend.config.StrategyProperties;
import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.entity.AccountEquityPeak;
import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import com.hqa.backend.entity.StrategyBatchSizing;
import com.hqa.backend.entity.StrategyNavClose;
import com.hqa.backend.repository.StrategyBatchSizingRepository;
import com.hqa.backend.repository.StrategyNavCloseRepository;
import com.hqa.backend.entity.TradingHalt;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.AccountEquityPeakRepository;
import com.hqa.backend.repository.StrategyAccountRepository;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyBatchRepository;
import com.hqa.backend.repository.TradingHaltRepository;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.transaction.PlatformTransactionManager;

/** In-memory repositories and mocked broker/account services shared by the strategy runtime tests. */
final class StrategyTestFixture {
    static final ZoneId KST = ZoneId.of("Asia/Seoul");
    static final String HASH = "a".repeat(64);
    static final LocalDate SESSION = LocalDate.parse("2026-11-02"); // Monday
    static final LocalDate DECISION = LocalDate.parse("2026-10-30");

    static final class MutableClock extends Clock {
        Instant now;
        MutableClock(String at) { set(at); }
        void set(String at) { now = OffsetDateTime.parse(at).toInstant(); }
        @Override public ZoneId getZone() { return KST; }
        @Override public Clock withZone(ZoneId zone) { return this; }
        @Override public Instant instant() { return now; }
    }

    final MutableClock clock = new MutableClock("2026-11-02T08:35:00+09:00");
    final StrategyProperties properties = new StrategyProperties();
    final StrategySessionClock sessions = new StrategySessionClock(KrxSessionCalendar.regular(), properties, clock);
    final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules()
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);

    final Map<String, StrategyAccount> accountRows = new LinkedHashMap<>();
    final Map<UUID, StrategyBatch> batchRows = new LinkedHashMap<>();
    final Map<UUID, StrategyBatchOrder> orderRows = new LinkedHashMap<>();
    final Map<String, TradingHalt> haltRows = new HashMap<>();
    final Map<String, Long> peakRows = new HashMap<>();
    final Map<UUID, StrategyBatchSizing> sizingRows = new LinkedHashMap<>();
    final Map<StrategyNavClose.Key, StrategyNavClose> closeRows = new LinkedHashMap<>();

    final StrategyAccountRepository accounts = mock(StrategyAccountRepository.class);
    final StrategyBatchRepository batches = mock(StrategyBatchRepository.class);
    final StrategyBatchOrderRepository orders = mock(StrategyBatchOrderRepository.class);
    final TradingHaltRepository halts = mock(TradingHaltRepository.class);
    final AccountEquityPeakRepository peaks = mock(AccountEquityPeakRepository.class);
    final PaperAccountSnapshotService snapshots = mock(PaperAccountSnapshotService.class);
    final PaperAccountGuard guard = mock(PaperAccountGuard.class);
    final KisClient kis = mock(KisClient.class);
    final PlatformTransactionManager transactions = mock(PlatformTransactionManager.class);
    final StrategyBatchSizingRepository sizings = mock(StrategyBatchSizingRepository.class);
    final StrategyNavCloseRepository navCloses = mock(StrategyNavCloseRepository.class);
    final User user = PaperTradeStoreTest.user();
    final Map<String, Object> snapshot = new HashMap<>();

    final StrategyTerminationService termination = new StrategyTerminationService(accounts, navCloses, orders, snapshots,
            sessions, properties);
    final StrategyRiskGuard risk = new StrategyRiskGuard(snapshots, peaks, orders, halts, properties, sessions, accounts,
            termination);
    final StrategyBatchService service = new StrategyBatchService(accounts, batches, orders, sizings, snapshots, guard, risk,
            sessions, properties, mapper, transactions);
    final StrategyBatchExecutor executor = newExecutor();
    final StrategyBatchReconciler reconciler = new StrategyBatchReconciler(properties, batches, orders, service, sessions,
            snapshots, kis);
    final StrategyOrderResolver resolver = new StrategyOrderResolver(orders, batches, service, snapshots, kis, sessions, mapper);

    /** A fresh executor over the same repositories, as after a process restart. */
    StrategyBatchExecutor newExecutor() {
        return new StrategyBatchExecutor(properties, batches, orders, accounts, service, risk, sessions, snapshots, kis, mapper,
                sizings);
    }

    StrategyTestFixture() {
        accountRows.put("u1", new StrategyAccount("u1", "hf001_f_both", "paper", true, now()));
        snapshot.putAll(Map.of("success", true, "userId", "u1", "equity", 100_000_000L, "dailyPnlPct", 0.0));
        when(snapshots.snapshot("u1")).thenAnswer(inv -> new HashMap<>(snapshot));
        when(snapshots.paperUser("u1")).thenReturn(user);
        when(snapshots.binding(user)).thenReturn("binding-1");
        when(guard.lock("u1")).thenReturn(user);
        when(guard.binding(user)).thenReturn("binding-1");
        when(kis.fetchAccessToken(eq("u1"), any())).thenReturn("token");

        when(accounts.findById(anyString())).thenAnswer(inv -> Optional.ofNullable(accountRows.get(inv.<String>getArgument(0))));
        when(accounts.save(any())).thenAnswer(inv -> {
            StrategyAccount account = inv.getArgument(0);
            accountRows.put(account.getUserId(), account);
            return account;
        });
        when(accounts.findAll()).thenAnswer(inv -> List.copyOf(accountRows.values()));
        when(sizings.saveAndFlush(any())).thenAnswer(inv -> {
            StrategyBatchSizing sizing = inv.getArgument(0);
            if (sizingRows.containsKey(sizing.getBatchId())) throw new org.springframework.dao.DataIntegrityViolationException("dup");
            sizingRows.put(sizing.getBatchId(), sizing);
            return sizing;
        });
        when(sizings.findById(any())).thenAnswer(inv -> Optional.ofNullable(sizingRows.get(inv.<UUID>getArgument(0))));
        when(sizings.existsById(any())).thenAnswer(inv -> sizingRows.containsKey(inv.<UUID>getArgument(0)));
        when(navCloses.saveAndFlush(any())).thenAnswer(inv -> {
            StrategyNavClose row = inv.getArgument(0);
            StrategyNavClose.Key key = new StrategyNavClose.Key(row.getAccountBinding(), row.getSessionDate(), row.getSource());
            if (closeRows.containsKey(key)) throw new org.springframework.dao.DataIntegrityViolationException("dup");
            closeRows.put(key, row);
            return row;
        });
        when(navCloses.findByAccountBindingAndSessionDateAndSource(anyString(), any(), anyString())).thenAnswer(inv ->
                Optional.ofNullable(closeRows.get(new StrategyNavClose.Key(inv.getArgument(0), inv.getArgument(1), inv.getArgument(2)))));
        when(navCloses.findByAccountBindingAndSessionDateGreaterThanEqualOrderBySessionDateAsc(anyString(), any())).thenAnswer(inv ->
                closeRows.values().stream().filter(row -> row.getAccountBinding().equals(inv.getArgument(0))
                        && !row.getSessionDate().isBefore(inv.getArgument(1)))
                        .sorted(Comparator.comparing(StrategyNavClose::getSessionDate)).toList());
        when(batches.findFirstByUserIdAndSessionDateAndPhaseAndStatusNot(anyString(), any(), anyString(), anyString()))
                .thenAnswer(inv -> batchRows.values().stream().filter(batch -> batch.getUserId().equals(inv.getArgument(0))
                        && batch.getSessionDate().equals(inv.getArgument(1)) && batch.getPhase().equals(inv.getArgument(2))
                        && !batch.getStatus().equals(inv.getArgument(3))).findFirst());
        when(accounts.existsById(anyString())).thenAnswer(inv -> accountRows.containsKey(inv.<String>getArgument(0)));

        when(batches.saveAndFlush(any())).thenAnswer(inv -> {
            StrategyBatch batch = inv.getArgument(0);
            batchRows.put(batch.getId(), batch);
            return batch;
        });
        when(batches.findById(any())).thenAnswer(inv -> Optional.ofNullable(batchRows.get(inv.<UUID>getArgument(0))));
        when(batches.findByIdempotencyKey(anyString())).thenAnswer(inv -> batchRows.values().stream()
                .filter(batch -> batch.getIdempotencyKey().equals(inv.getArgument(0))).findFirst());
        when(batches.findBySessionDateAndStatusInOrderByCreatedAtAsc(any(), anyList())).thenAnswer(inv -> batchRows.values().stream()
                .filter(batch -> batch.getSessionDate().equals(inv.getArgument(0))
                        && ((List<?>) inv.getArgument(1)).contains(batch.getStatus())).toList());
        when(batches.existsByUserIdAndSessionDateAndPhaseAndStatusNot(anyString(), any(), anyString(), anyString()))
                .thenAnswer(inv -> batchRows.values().stream().anyMatch(batch -> batch.getUserId().equals(inv.getArgument(0))
                        && batch.getSessionDate().equals(inv.getArgument(1)) && batch.getPhase().equals(inv.getArgument(2))
                        && !batch.getStatus().equals(inv.getArgument(3))));
        when(batches.findStaleBefore(any())).thenAnswer(inv -> batchRows.values().stream()
                .filter(batch -> batch.getSessionDate().isBefore(inv.getArgument(0))
                        && (List.of("QUEUED", "SUBMITTING", "SUBMITTED").contains(batch.getStatus())
                        || orderRows.values().stream().anyMatch(order -> order.getBatchId().equals(batch.getId())
                        && List.of("PENDING", "SUBMITTED", "PARTIALLY_FILLED").contains(order.getStatus())))).toList());
        when(batches.findFirstByStrategyIdOrderByCreatedAtDesc(anyString())).thenAnswer(inv -> batchRows.values().stream()
                .filter(batch -> batch.getStrategyId().equals(inv.getArgument(0)))
                .max(Comparator.comparing(StrategyBatch::getCreatedAt)));

        when(orders.saveAndFlush(any())).thenAnswer(inv -> {
            StrategyBatchOrder order = inv.getArgument(0);
            orderRows.put(order.getId(), order);
            return order;
        });
        when(orders.saveAllAndFlush(anyIterable())).thenAnswer(inv -> {
            List<StrategyBatchOrder> saved = new ArrayList<>();
            for (Object item : (Iterable<?>) inv.getArgument(0)) {
                StrategyBatchOrder order = (StrategyBatchOrder) item;
                orderRows.put(order.getId(), order);
                saved.add(order);
            }
            return saved;
        });
        when(orders.findById(any())).thenAnswer(inv -> Optional.ofNullable(orderRows.get(inv.<UUID>getArgument(0))));
        when(orders.findByStatusOrderBySubmittedAtAsc(anyString())).thenAnswer(inv -> orderRows.values().stream()
                .filter(order -> order.getStatus().equals(inv.getArgument(0))).toList());
        when(orders.findByOrderDateAndBrokerOrderIdIsNotNull(any())).thenAnswer(inv -> orderRows.values().stream()
                .filter(order -> order.getBrokerOrderId() != null && inv.getArgument(0).equals(order.getOrderDate())).toList());
        when(orders.existsOrderForUserWithStatusIn(anyString(), anyList())).thenAnswer(inv -> orderRows.values().stream()
                .anyMatch(order -> {
                    StrategyBatch batch = batchRows.get(order.getBatchId());
                    return batch != null && batch.getUserId().equals(inv.getArgument(0))
                            && ((List<?>) inv.getArgument(1)).contains(order.getStatus());
                }));
        when(orders.findByBatchIdOrderBySeqAsc(any())).thenAnswer(inv -> orderRows.values().stream()
                .filter(order -> order.getBatchId().equals(inv.getArgument(0)))
                .sorted(Comparator.comparingInt(StrategyBatchOrder::getSeq)).toList());
        when(orders.countOrdersForUserOn(anyString(), any())).thenAnswer(inv -> ordersOf(inv.getArgument(0), inv.getArgument(1))
                .stream().filter(order -> !"SKIPPED".equals(order.getStatus())).count());
        when(orders.countCancelsForUserOn(anyString(), any())).thenAnswer(inv -> ordersOf(inv.getArgument(0), inv.getArgument(1))
                .stream().filter(order -> order.getCancelRequestedAt() != null).count());

        when(halts.findById(anyString())).thenAnswer(inv -> Optional.ofNullable(haltRows.get(inv.<String>getArgument(0))));
        when(halts.save(any())).thenAnswer(inv -> {
            TradingHalt halt = inv.getArgument(0);
            haltRows.put(halt.getScope(), halt);
            return halt;
        });
        when(halts.findAllByOrderByScopeAsc()).thenAnswer(inv -> haltRows.values().stream()
                .sorted(Comparator.comparing(TradingHalt::getScope)).toList());

        when(peaks.findById(anyString())).thenAnswer(inv -> Optional.ofNullable(peakRows.get(inv.<String>getArgument(0)))
                .map(value -> new AccountEquityPeak(inv.getArgument(0), value, now())));
        when(peaks.raisePeak(anyString(), anyLong(), any())).thenAnswer(inv -> {
            peakRows.merge(inv.getArgument(0), inv.<Long>getArgument(1), Math::max);
            return 1;
        });
    }

    OffsetDateTime now() {
        return OffsetDateTime.now(clock).atZoneSameInstant(KST).toOffsetDateTime();
    }

    private List<StrategyBatchOrder> ordersOf(String userId, LocalDate day) {
        return orderRows.values().stream().filter(order -> {
            StrategyBatch batch = batchRows.get(order.getBatchId());
            return batch != null && batch.getUserId().equals(userId) && batch.getSessionDate().equals(day);
        }).toList();
    }

    void halt(String scope) {
        TradingHalt halt = new TradingHalt(scope);
        halt.setHalted(true);
        halt.setReason("test");
        halt.setUpdatedAt(now());
        haltRows.put(scope, halt);
    }

    static StrategyBatchRequest.Order sell(int seq, String code, long quantity, long price) {
        return new StrategyBatchRequest.Order(seq, code, "SELL", quantity, price, 12_000_000_000.0);
    }

    static StrategyBatchRequest.Order buy(int seq, String code, long quantity, long price) {
        return new StrategyBatchRequest.Order(seq, code, "BUY", quantity, price, 900_000_000.0);
    }

    static StrategyBatchRequest request(boolean dryRun, String phase, LocalDate session, List<StrategyBatchRequest.Order> lines) {
        return request(dryRun, phase, session, HASH, lines);
    }

    static StrategyBatchRequest request(boolean dryRun, String phase, LocalDate session, LocalDate decision,
            List<StrategyBatchRequest.Order> lines) {
        String key = StrategyBatchService.idempotencyKey("hf001_f_both", HASH, session.toString(), phase, "u1");
        return new StrategyBatchRequest("hf001_f_both", "u1", HASH, decision, session, phase, key, dryRun, 100_000_000L, lines);
    }

    static StrategyBatchRequest request(boolean dryRun, String phase, LocalDate session, String hash,
            List<StrategyBatchRequest.Order> lines) {
        String key = StrategyBatchService.idempotencyKey("hf001_f_both", hash, session.toString(), phase, "u1");
        return new StrategyBatchRequest("hf001_f_both", "u1", hash, DECISION, session, phase, key, dryRun, 100_000_000L, lines);
    }

    static StrategyBatchRequest request(boolean dryRun, List<StrategyBatchRequest.Order> lines) {
        return request(dryRun, "OPEN", SESSION, lines);
    }

    static List<StrategyBatchRequest.Order> basket() {
        return List.of(sell(1, "005930", 10, 70_000), buy(2, "0126Z0", 3, 51_000));
    }

    /** Accepts a QUEUED OPEN batch at the current clock, which must be inside the OPEN window. */
    StrategyBatch queued(List<StrategyBatchRequest.Order> lines) {
        Map<String, Object> response = service.create(request(false, lines));
        if (!"QUEUED".equals(response.get("status"))) throw new IllegalStateException("not queued: " + response);
        return batchRows.get(UUID.fromString((String) response.get("batchId")));
    }

    List<StrategyBatchOrder> ordersOf(StrategyBatch batch) {
        return orders.findByBatchIdOrderBySeqAsc(batch.getId());
    }

    StrategyBatchOrder order(StrategyBatch batch, String code) {
        return ordersOf(batch).stream().filter(order -> order.getStockCode().equals(code)).findFirst().orElseThrow();
    }

    static Map<String, Object> accepted(String odno) {
        return Map.of("success", true, "response", Map.of("rt_cd", "0", "output",
                Map.of("ODNO", odno, "KRX_FWDG_ORD_ORGNO", "06010")));
    }
}
