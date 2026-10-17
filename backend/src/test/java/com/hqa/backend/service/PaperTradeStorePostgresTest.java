package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import com.hqa.backend.repository.UserRepository;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.ImportAutoConfiguration;
import org.springframework.boot.autoconfigure.jackson.JacksonAutoConfiguration;
import org.springframework.boot.autoconfigure.jdbc.JdbcTemplateAutoConfiguration;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Opt-in PostgreSQL checks that the account lock serializes concurrent plan saves and trigger
 * claims: no double sell, no cash reserved twice, one plan per stock. Each call commits on its
 * own (no test transaction), so concurrent callers see each other the way production does.
 */
@DataJpaTest(properties = {"spring.jpa.hibernate.ddl-auto=validate", "hqa.kis-enc-key=postgres-test-only-key"})
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@EnabledIfEnvironmentVariable(named = "HQA_TEST_DATABASE_URL", matches = "jdbc:postgresql:.*")
@Transactional(propagation = Propagation.NOT_SUPPORTED)
@Import({PaperTradeStore.class, PaperAccountGuard.class, SecretCipher.class})
@EnableConfigurationProperties(HqaProperties.class)
@ImportAutoConfiguration({JacksonAutoConfiguration.class, JdbcTemplateAutoConfiguration.class})
class PaperTradeStorePostgresTest {
    @Autowired PaperTradeStore store;
    @Autowired UserRepository users;
    @Autowired TradeSignalRepository signals;
    @Autowired TradeSignalExecutionRepository executions;
    @Autowired SecretCipher cipher;
    @Autowired JdbcTemplate jdbc;
    private final List<String> created = new ArrayList<>();

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry properties) {
        properties.add("spring.datasource.url", () -> System.getenv("HQA_TEST_DATABASE_URL"));
        properties.add("spring.datasource.username", () -> System.getenv("HQA_TEST_DATABASE_USERNAME"));
        properties.add("spring.datasource.password", () -> System.getenv("HQA_TEST_DATABASE_PASSWORD"));
    }

    @AfterEach
    void removeTestRows() {
        for (String userId : created) {
            for (String table : List.of("trade_signal_executions", "trade_plan_receipts", "trade_signals", "paper_broker_accounts")) {
                jdbc.update("DELETE FROM public." + table + " WHERE user_id = ?", userId);
            }
            jdbc.update("DELETE FROM public.user_secrets WHERE user_id IN (SELECT id FROM public.users WHERE user_id = ?)", userId);
            jdbc.update("DELETE FROM public.users WHERE user_id = ?", userId);
        }
    }

    @Test
    void concurrentProtectiveTriggersForOnePlanCreateOneSellOrder() throws Exception {
        String user = paperUser();
        OffsetDateTime now = OffsetDateTime.now();
        TradeSignal plan = store.save(hold(user, "005930", now), account(user, 10, 100_000, 0), now);
        Callable<Object> claim = () -> store.claim(plan.getId(), 1, TradeConditions.TriggerType.EXIT, "stop",
                account(user, 10, 100_000, 0), 90, 0, 0, null, OffsetDateTime.now());
        List<Object> outcomes = race(Collections.nCopies(8, claim));
        assertThat(outcomes).filteredOn(TradeSignalExecution.class::isInstance).hasSize(1);
        assertThat(outcomes).filteredOn(Throwable.class::isInstance).hasSize(7).allSatisfy(failure -> {
            assertThat(failure).isInstanceOf(IllegalStateException.class);
            assertThat(((Throwable) failure).getMessage()).isIn("ORDER_RECONCILIATION_REQUIRED", "TRIGGER_ALREADY_PENDING");
        });
        assertThat(executions.findBySignalId(plan.getId())).singleElement()
                .satisfies(sell -> assertThat(sell.getSubmittedQuantity()).isEqualTo(10));
    }

    @Test
    void concurrentEntriesOnOneAccountNeverReserveMoreThanItsCash() throws Exception {
        String user = paperUser();
        OffsetDateTime now = OffsetDateTime.now();
        // 10% of 100,000 equity is 10,000 per entry; five entries race for 25,000 of cash.
        List<TradeSignal> plans = new ArrayList<>();
        for (String code : List.of("005930", "000660", "035420", "051910", "068270")) {
            plans.add(store.save(buy(user, code, UUID.randomUUID().toString(), now), account(user, 0, 100_000, 25_000), now));
        }
        List<Callable<Object>> claims = plans.stream().<Callable<Object>>map(plan -> () -> store.claim(plan.getId(), 1,
                TradeConditions.TriggerType.ENTRY, "entry", account(user, 0, 100_000, 25_000), 100, 25_000, 1_000,
                null, OffsetDateTime.now())).toList();
        List<Object> outcomes = race(claims);
        List<TradeSignalExecution> buys = outcomes.stream().filter(TradeSignalExecution.class::isInstance)
                .map(TradeSignalExecution.class::cast).toList();
        assertThat(buys.stream().mapToLong(TradeSignalExecution::getReservedCash).sum()).isEqualTo(25_000);
        assertThat(executions.reservedCashForUser(user)).isEqualTo(25_000);
        assertThat(buys.stream().map(TradeSignalExecution::getSubmittedQuantity).sorted().toList()).isEqualTo(List.of(50, 100, 100));
        assertThat(outcomes).filteredOn(Throwable.class::isInstance).hasSize(2).allSatisfy(failure ->
                assertThat(((Throwable) failure).getMessage()).isEqualTo("NO_ORDERABLE_QUANTITY"));
    }

    @Test
    void concurrentCopiesOfOnePublishedPlanCreateOnePlan() throws Exception {
        String user = paperUser();
        OffsetDateTime now = OffsetDateTime.now();
        accountInUse(user, now);
        InternalTradeSignalRequest request = buy(user, "005930", "copy-" + UUID.randomUUID(), now);
        Callable<Object> publish = () -> store.save(request, account(user, 0, 100_000, 50_000), OffsetDateTime.now());
        List<Object> outcomes = race(Collections.nCopies(6, publish));
        assertThat(outcomes).allSatisfy(outcome -> assertThat(outcome).isInstanceOf(TradeSignal.class));
        assertThat(outcomes.stream().map(outcome -> ((TradeSignal) outcome).getId()).distinct()).hasSize(1);
        assertThat(signals.findByUserIdAndStatusIn(user, PaperTradeStore.ACTIVE)).hasSize(2);
    }

    @Test
    void concurrentFirstPlansForOneStockLeaveOneActivePlan() throws Exception {
        String user = paperUser();
        OffsetDateTime now = OffsetDateTime.now();
        accountInUse(user, now);
        List<Callable<Object>> saves = new ArrayList<>();
        for (int i = 0; i < 6; i++) {
            InternalTradeSignalRequest request = buy(user, "005930", "first-" + i + "-" + UUID.randomUUID(), now);
            saves.add(() -> store.save(request, account(user, 0, 100_000, 50_000), OffsetDateTime.now()));
        }
        List<Object> outcomes = race(saves);
        assertThat(outcomes).filteredOn(TradeSignal.class::isInstance).hasSize(1);
        // The losers are refused by the store's own rules (same analysis time, same version), not
        // by a unique-index violation surfacing as a database error.
        assertThat(outcomes).filteredOn(Throwable.class::isInstance).hasSize(5).allSatisfy(failure -> {
            assertThat(failure).isInstanceOf(IllegalArgumentException.class);
            assertThat(((Throwable) failure).getMessage()).isIn("STALE_ANALYSIS", "planVersion must increase on replacement");
        });
        assertThat(signals.findByUserIdAndStatusIn(user, PaperTradeStore.ACTIVE)).hasSize(2);
    }

    /** A plan for another stock first, so the race below runs on an account already in use. */
    private void accountInUse(String user, OffsetDateTime now) {
        store.save(buy(user, "000660", "earlier-" + UUID.randomUUID(), now), account(user, 0, 100_000, 50_000), now);
    }

    private String paperUser() {
        String userId = "pg-" + UUID.randomUUID();
        User user = new User();
        user.setUserId(userId);
        user.setFirstName("테스트");
        user.setLastName("사용자");
        user.setPassword("test-only");
        user.setAutoTradeEnabled(true);
        UserSecret secret = new UserSecret();
        secret.setKisAppKey(cipher.encrypt("app-" + UUID.randomUUID()));
        secret.setKisAppSecret(cipher.encrypt("secret-" + UUID.randomUUID()));
        secret.setKisAccountNo(cipher.encrypt(Integer.toString(ThreadLocalRandom.current().nextInt(10_000_000, 100_000_000))));
        secret.setKisAccountProductCode("01");
        user.setSecret(secret);
        users.saveAndFlush(user);
        created.add(userId);
        return userId;
    }

    private static Map<String, Object> account(String user, int held, long equity, long cash) {
        Map<String, Object> account = new HashMap<>(Map.of("success", true, "userId", user, "equity", equity,
                "orderableCash", cash, "reservedCash", 0L, "dailyPnlPct", 0.0, "entryEligible", true));
        account.put("capturedAt", OffsetDateTime.now().toString());
        account.put("holdings", held == 0 ? List.of() : List.of(Map.of("stockCode", "005930", "quantity", held,
                "sellableQuantity", held, "avgPrice", 100.0, "currentPrice", 100, "evalAmount", held * 100L, "pnlRate", 0.0)));
        return account;
    }

    private static InternalTradeSignalRequest buy(String user, String code, String key, OffsetDateTime asOf) {
        Map<String, Object> stop = Map.of("field", "current_price", "operator", "<=", "value", 90);
        Map<String, Object> payload = Map.of("schema_version", 2,
                "entry_conditions", List.of(Map.of("id", "entry", "all", List.of(Map.of("field", "current_price", "operator", ">=", "value", 100)))),
                "exit_conditions", List.of(Map.of("id", "stop", "all", List.of(stop))));
        return new InternalTradeSignalRequest(user, "luna", "short", null, null, code, code, "BUY", null, null, "MEDIUM",
                "10%", 100L, "90", "reason", asOf.plusMinutes(10), Map.of(), Map.of("stop_loss_price", 90), payload, key, 1,
                asOf.plusMinutes(10), asOf.plusDays(3), 10.0, "PAPER", key, asOf);
    }

    private static InternalTradeSignalRequest hold(String user, String code, OffsetDateTime asOf) {
        Map<String, Object> payload = Map.of("schema_version", 2, "exit_conditions", List.of(Map.of("id", "stop",
                "all", List.of(Map.of("field", "current_price", "operator", "<=", "value", 90)))));
        String key = "hold-" + UUID.randomUUID();
        return new InternalTradeSignalRequest(user, "luna", "short", null, null, code, code, "HOLD", null, null, "MEDIUM",
                "10%", 100L, "90", "reason", asOf.plusMinutes(10), Map.of(), Map.of("stop_loss_price", 90), payload, key, 1,
                asOf.plusMinutes(10), asOf.plusDays(3), 10.0, "PAPER", key, asOf);
    }

    /** Starts every task at once and returns each one's result or the exception it threw. */
    private static <T> List<Object> race(List<Callable<T>> tasks) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(tasks.size());
        try {
            CountDownLatch start = new CountDownLatch(1);
            List<Future<T>> futures = new ArrayList<>();
            for (Callable<T> task : tasks) {
                futures.add(pool.submit(() -> {
                    start.await();
                    return task.call();
                }));
            }
            start.countDown();
            List<Object> outcomes = new ArrayList<>();
            for (Future<T> future : futures) {
                try { outcomes.add(future.get(60, TimeUnit.SECONDS)); }
                catch (ExecutionException ex) { outcomes.add(ex.getCause()); }
            }
            return outcomes;
        } finally {
            pool.shutdownNow();
        }
    }
}
