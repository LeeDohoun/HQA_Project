package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.ErrorResponse;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.dto.InternalTradeSignalResponse;
import com.hqa.backend.entity.TradePlanReceipt;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.*;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;
import org.springframework.test.util.ReflectionTestUtils;

/**
 * Offline end-to-end check of PAPER protection: the real PaperTradeLifecycle and PaperTradeStore
 * behind an HTTP server that speaks the monitor's internal API, with in-memory repositories, a
 * simulated KIS paper broker and a clock that scripts/paper_lifecycle_sim.py advances while it
 * drives the real Python SignalMonitor through price scenarios. Skipped unless HQA_SIM_PYTHON
 * names a Python with the project's requirements, for example from backend/:
 *
 * <pre>HQA_SIM_PYTHON=$PWD/../venv/bin/python mvn -q test -Dtest=PaperLifecycleSimulationTest</pre>
 *
 * The scenario report is written to target/paper-lifecycle-sim.txt.
 */
@EnabledIfEnvironmentVariable(named = "HQA_SIM_PYTHON", matches = ".+")
class PaperLifecycleSimulationTest {
    static final ZoneId KST = ZoneId.of("Asia/Seoul");
    static final OffsetDateTime START = OffsetDateTime.parse("2026-10-12T10:00:00+09:00");

    static final class MutableClock extends Clock {
        Instant now = START.toInstant();
        @Override public ZoneId getZone() { return KST; }
        @Override public Clock withZone(ZoneId zone) { return this; }
        @Override public Instant instant() { return now; }
    }

    static final class Order {
        String odno, side, date;
        int qty, filled, cancelled;
        long limit;
        String submittedAt;
        int remaining() { return qty - filled - cancelled; }
    }

    final MutableClock clock = new MutableClock();
    /** Configured like the backend's Spring mapper (application.yml), which the services also receive. */
    final ObjectMapper json = new ObjectMapper().findAndRegisterModules()
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    final Validator validator = Validation.buildDefaultValidatorFactory().getValidator();
    final Map<String, TradePlanReceipt> receiptRows = new LinkedHashMap<>();
    /** Other holdings in the account: listed in snapshots and quoted, never traded. */
    final List<Map<String, Object>> extra = new ArrayList<>();
    final Map<String, TradeSignal> signalRows = new LinkedHashMap<>();
    final Map<String, TradeSignalExecution> executionRows = new LinkedHashMap<>();
    final List<Order> orders = new ArrayList<>();
    final List<Map<String, Object>> events = new ArrayList<>();
    long price = 100;
    int held = 0;
    double avg = 0;
    long cash = 10_000_000;
    boolean fills = true;
    int partial = 0;          // most shares one order fills per second (0: all)
    int pollerPhase = 0;      // second within each 20 s at which the backend's schedulers run
    int rateLimited = 0;      // next order calls KIS answers with EGW00201
    int orderSeq = 0, signalSeq = 0, executionSeq = 0;

    final TradeSignalRepository signals = mock(TradeSignalRepository.class);
    final TradeSignalExecutionRepository executions = mock(TradeSignalExecutionRepository.class);
    final PaperAccountGuard guard = mock(PaperAccountGuard.class);
    final TradePlanReceiptRepository receipts = mock(TradePlanReceiptRepository.class);
    final PaperAccountSnapshotService accounts = mock(PaperAccountSnapshotService.class);
    final KisClient kis = mock(KisClient.class);
    final User user = PaperTradeStoreTest.user();
    PaperTradeStore store;
    PaperTradeLifecycle lifecycle;

    OffsetDateTime now() { return OffsetDateTime.ofInstant(clock.now, KST); }

    void event(String kind, Object... pairs) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("t", Duration.between(START.toInstant(), clock.now).toSeconds());
        row.put("kind", kind);
        for (int i = 0; i + 1 < pairs.length; i += 2) row.put(String.valueOf(pairs[i]), pairs[i + 1]);
        events.add(row);
    }

    int sellable() {
        int working = orders.stream().filter(o -> "SELL".equals(o.side)).mapToInt(Order::remaining).sum();
        return Math.max(0, held - working);
    }

    Map<String, Object> snapshot() {
        Map<String, Object> account = new HashMap<>();
        account.put("success", true);
        account.put("userId", "u1");
        account.put("accountMode", "PAPER");
        account.put("equity", cash + held * price);
        account.put("orderableCash", cash);
        account.put("reservedCash", 0L);
        account.put("dailyPnlPct", 0.0);
        account.put("entryEligible", true);
        account.put("capturedAt", now().toString());
        List<Map<String, Object>> holdings = new ArrayList<>();
        if (held > 0) {
            holdings.add(Map.of("stockCode", "005930", "quantity", held, "sellableQuantity", sellable(), "avgPrice", avg,
                    "currentPrice", price, "evalAmount", held * price, "pnlRate", (price / avg - 1) * 100));
        }
        for (Map<String, Object> other : extra) {
            int quantity = ((Number) other.get("quantity")).intValue();
            double average = ((Number) other.get("avgPrice")).doubleValue();
            long quote = ((Number) other.get("price")).longValue();
            holdings.add(Map.of("stockCode", other.get("stockCode"), "quantity", quantity, "sellableQuantity", quantity,
                    "avgPrice", average, "currentPrice", quote, "evalAmount", quantity * quote, "pnlRate", (quote / average - 1) * 100));
        }
        account.put("holdings", holdings);
        account.put("equity", cash + held * price + extra.stream()
                .mapToLong(o -> ((Number) o.get("quantity")).longValue() * ((Number) o.get("price")).longValue()).sum());
        return account;
    }

    void wire() {
        store = new PaperTradeStore(signals, executions, guard, json, receipts, 1, 0.5);
        lifecycle = new PaperTradeLifecycle(signals, executions, accounts, store, kis, json, clock);
        when(guard.lock("u1")).thenReturn(user);
        when(guard.binding(any())).thenReturn("binding");
        when(accounts.paperUser("u1")).thenReturn(user);
        when(accounts.binding(any())).thenReturn("binding");
        when(accounts.snapshot("u1")).thenAnswer(inv -> snapshot());
        when(receipts.existsById(anyString())).thenAnswer(inv -> receiptRows.containsKey((String) inv.getArgument(0)));
        when(receipts.findById(anyString())).thenAnswer(inv -> Optional.ofNullable(receiptRows.get((String) inv.getArgument(0))));
        when(receipts.saveAndFlush(any())).thenAnswer(inv -> {
            TradePlanReceipt receipt = inv.getArgument(0);
            receiptRows.put((String) ReflectionTestUtils.getField(receipt, "id"), receipt);
            return receipt;
        });

        org.mockito.stubbing.Answer<TradeSignal> saveSignal = inv -> {
            TradeSignal signal = inv.getArgument(0);
            if (signal.getId() == null) ReflectionTestUtils.setField(signal, "id", "s" + (++signalSeq));
            signalRows.put(signal.getId(), signal);
            return signal;
        };
        when(signals.saveAndFlush(any())).thenAnswer(saveSignal);
        when(signals.save(any())).thenAnswer(saveSignal);
        when(signals.findById(anyString())).thenAnswer(inv -> Optional.ofNullable(signalRows.get((String) inv.getArgument(0))));
        when(signals.ownerOf(anyString())).thenReturn(Optional.of("u1"));
        when(signals.findByUserIdAndStatusIn(anyString(), anyList())).thenAnswer(inv -> signalRows.values().stream()
                .filter(s -> ((List<?>) inv.getArgument(1)).contains(s.getStatus())).toList());
        when(signals.findByStatusIn(anyList(), any(Pageable.class))).thenAnswer(inv -> {
            List<TradeSignal> rows = signalRows.values().stream()
                    .filter(s -> ((List<?>) inv.getArgument(0)).contains(s.getStatus())).toList();
            return new PageImpl<>(rows, inv.getArgument(1), rows.size());
        });

        when(executions.saveAndFlush(any())).thenAnswer(inv -> {
            TradeSignalExecution execution = inv.getArgument(0);
            if (execution.getId() == null) ReflectionTestUtils.setField(execution, "id", "e" + (++executionSeq));
            executionRows.put(execution.getId(), execution);
            return execution;
        });
        when(executions.findById(anyString())).thenAnswer(inv -> Optional.ofNullable(executionRows.get((String) inv.getArgument(0))));
        when(executions.ownerOf(anyString())).thenReturn(Optional.of("u1"));
        when(executions.findBySignalId(anyString())).thenAnswer(inv -> executionRows.values().stream()
                .filter(e -> e.getSignalId().equals(inv.getArgument(0))).toList());
        when(executions.findByUserIdAndStatusIn(anyString(), anyList())).thenAnswer(inv -> executionRows.values().stream()
                .filter(e -> ((List<?>) inv.getArgument(1)).contains(e.getStatus())).toList());
        when(executions.findByStatusInOrderBySubmittedAtAsc(anyList())).thenAnswer(inv -> executionRows.values().stream()
                .filter(e -> ((List<?>) inv.getArgument(0)).contains(e.getStatus()))
                .sorted(Comparator.comparing(TradeSignalExecution::getSubmittedAt)).toList());
        when(executions.countByUserIdAndOrderSideAndSubmittedAtAfter(anyString(), anyString(), any())).thenAnswer(inv ->
                executionRows.values().stream().filter(e -> inv.getArgument(1).equals(e.getOrderSide())
                        && e.getSubmittedAt().isAfter(inv.getArgument(2))).count());
        when(executions.reservedCashForUser(anyString())).thenAnswer(inv -> executionRows.values().stream()
                .mapToLong(e -> e.getReservedCash() == null ? 0 : e.getReservedCash()).sum());

        when(kis.fetchAccessToken(anyString(), any())).thenReturn("token");
        when(kis.inquireCurrentPrice(anyString(), any(), anyString(), anyString())).thenAnswer(inv -> quote(inv.getArgument(3)));
        when(kis.paperPurchasingPower(anyString(), any(), anyString(), anyString(), anyLong())).thenAnswer(inv ->
                Map.of("cash", cash, "quantity", cash / (long) inv.getArgument(4)));
        when(kis.paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), anyString())).thenAnswer(inv -> {
            int quantity = inv.getArgument(4);
            long limit = inv.getArgument(5);
            String side = inv.getArgument(6);
            if (!"005930".equals(inv.getArgument(3))) {
                event("broker_refused", "side", side, "qty", quantity, "stockCode", inv.getArgument(3));
                return Map.of("success", false, "response", Map.of("rt_cd", "1", "msg_cd", "SIM_UNTRADED", "msg1", "not simulated"));
            }
            if (rateLimited > 0) {
                rateLimited--;
                event("rate_limited", "side", side, "qty", quantity);
                return Map.of("success", false, "response", Map.of("rt_cd", "1", "msg_cd", "EGW00201", "msg1", "rate"));
            }
            if ("SELL".equals(side) && quantity > sellable()) {
                event("broker_refused", "side", side, "qty", quantity, "sellable", sellable());
                return Map.of("success", false, "response", Map.of("rt_cd", "1", "msg_cd", "APBK0400", "msg1", "sellable exceeded"));
            }
            Order order = new Order();
            order.odno = "o" + (++orderSeq);
            order.side = side;
            order.qty = quantity;
            order.limit = limit;
            order.date = now().toLocalDate().format(DateTimeFormatter.BASIC_ISO_DATE);
            order.submittedAt = now().toString();
            orders.add(order);
            event("order", "odno", order.odno, "side", side, "qty", quantity, "limit", limit);
            return Map.of("success", true, "response", Map.of("rt_cd", "0",
                    "output", Map.of("ODNO", order.odno, "KRX_FWDG_ORD_ORGNO", "org")));
        });
        when(kis.paperOrders(anyString(), any(), anyString(), any(), any())).thenAnswer(inv -> orders.stream().map(order -> {
            Map<String, Object> row = new HashMap<>();
            row.put("odno", order.odno);
            row.put("ord_dt", order.date);
            row.put("pdno", "005930");
            row.put("sll_buy_dvsn_cd", "SELL".equals(order.side) ? "01" : "02");
            row.put("ord_qty", String.valueOf(order.qty));
            row.put("tot_ccld_qty", String.valueOf(order.filled));
            row.put("rmn_qty", String.valueOf(order.remaining()));
            row.put("cnc_cfrm_qty", String.valueOf(order.cancelled));
            row.put("rjct_qty", "0");
            row.put("cncl_yn", order.cancelled > 0 ? "Y" : "N");
            row.put("avg_prvs", String.valueOf(order.filled > 0 ? order.limit : 0));
            row.put("ord_gno_brno", "org");
            return row;
        }).toList());
        when(kis.cancelPaperOrder(anyString(), any(), anyString(), anyString(), anyString(), anyInt())).thenAnswer(inv -> {
            String odno = inv.getArgument(3);
            Order order = orders.stream().filter(o -> o.odno.equals(odno)).findFirst().orElse(null);
            if (order == null || order.remaining() <= 0) {
                event("cancel_refused", "odno", odno);
                return Map.of("success", false, "response", Map.of("rt_cd", "1", "msg_cd", "APBK0918"));
            }
            event("cancel", "odno", odno, "remaining", order.remaining());
            order.cancelled += order.remaining();
            return Map.of("success", true, "response", Map.of("rt_cd", "0"));
        });
    }

    long quote(String stockCode) {
        if ("005930".equals(stockCode)) return price;
        return extra.stream().filter(o -> stockCode.equals(o.get("stockCode")))
                .map(o -> ((Number) o.get("price")).longValue()).findFirst().orElseThrow();
    }

    void reset(Map<String, Object> body) {
        clock.now = START.toInstant();
        signalRows.clear();
        executionRows.clear();
        receiptRows.clear();
        extra.clear();
        @SuppressWarnings("unchecked") List<Map<String, Object>> others = (List<Map<String, Object>>) body.getOrDefault("extra", List.of());
        extra.addAll(others);
        orders.clear();
        events.clear();
        orderSeq = signalSeq = executionSeq = 0;
        price = ((Number) body.getOrDefault("price", 100)).longValue();
        held = ((Number) body.getOrDefault("held", 0)).intValue();
        avg = ((Number) body.getOrDefault("avg", 0)).doubleValue();
        cash = ((Number) body.getOrDefault("cash", 10_000_000)).longValue();
        fills = !Boolean.FALSE.equals(body.get("fills"));
        partial = ((Number) body.getOrDefault("partial", 0)).intValue();
        pollerPhase = ((Number) body.getOrDefault("pollerPhase", 0)).intValue();
        rateLimited = 0;
    }

    /** One simulated second: limit orders fill when the price reaches them, then the backend's schedulers run every 20 s. */
    void tick() {
        clock.now = clock.now.plusSeconds(1);
        if (fills) {
            for (Order order : orders) {
                int open = order.remaining();
                if (open <= 0) continue;
                if (partial > 0) open = Math.min(open, partial);
                boolean reaches = "SELL".equals(order.side) ? price >= order.limit : price <= order.limit;
                if (!reaches) continue;
                order.filled += open;
                if ("SELL".equals(order.side)) {
                    held -= open;
                    cash += open * order.limit;
                    if (held == 0) avg = 0;
                } else {
                    avg = (avg * held + order.limit * (double) open) / (held + open);
                    held += open;
                    cash -= open * order.limit;
                }
                event("fill", "odno", order.odno, "side", order.side, "qty", open, "price", order.limit);
            }
        }
        long elapsed = Duration.between(START.toInstant(), clock.now).toSeconds();
        if (elapsed % 20 == pollerPhase) {
            lifecycle.expireEntries();
            lifecycle.reconcilePendingOrders();
        }
    }

    Map<String, Object> plan(Map<String, Object> body) {
        String action = String.valueOf(body.getOrDefault("action", "HOLD"));
        int version = ((Number) body.getOrDefault("version", 1)).intValue();
        double stop = ((Number) body.getOrDefault("stop", 90)).doubleValue();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("schema_version", 2);
        payload.put("exit_conditions", List.of(Map.of("id", "stop", "all",
                List.of(Map.of("field", "current_price", "operator", "<=", "value", stop)))));
        List<Map<String, Object>> reduce = new ArrayList<>();
        @SuppressWarnings("unchecked") List<Map<String, Object>> tiers = (List<Map<String, Object>>) body.getOrDefault("reduce", List.of());
        for (Map<String, Object> tier : tiers) {
            reduce.add(Map.of("id", tier.get("id"), "reduce_fraction", tier.get("fraction"),
                    "all", List.of(Map.of("field", "pnl_rate", "operator", ">=", "value", tier.get("pnl")))));
        }
        if (!reduce.isEmpty()) payload.put("reduce_conditions", reduce);
        if (body.get("invalidation") != null) {
            payload.put("invalidation_conditions", List.of(Map.of("id", "invalid", "all",
                    List.of(Map.of("field", "current_price", "operator", "<=", "value", body.get("invalidation"))))));
        }
        if ("BUY".equals(action)) {
            payload.put("entry_conditions", List.of(Map.of("id", "entry", "all",
                    List.of(Map.of("field", "current_price", "operator", ">=", "value", body.get("entry"))))));
        }
        OffsetDateTime asOf = now();
        OffsetDateTime entryUntil = asOf.plusSeconds(((Number) body.getOrDefault("entrySeconds", 30)).longValue());
        Object plannedIn = body.get("plannedExitSeconds");
        OffsetDateTime planned = plannedIn == null ? asOf.plusDays(3) : asOf.plusSeconds(((Number) plannedIn).longValue());
        String key = body.getOrDefault("key", "p") + "-v" + version;
        long signalPrice = ((Number) body.getOrDefault("signalPrice", price)).longValue();
        InternalTradeSignalRequest request = new InternalTradeSignalRequest("u1", "luna", "short", null, null, "005930",
                "Samsung", action, null, null, "MEDIUM", "10%", signalPrice, Double.toString(stop), "sim",
                entryUntil, Map.of(), Map.of("stop_loss_price", stop), payload, key, version, entryUntil, planned, 10.0,
                "PAPER", key, asOf);
        TradeSignal saved = lifecycle.save(request);
        event("plan", "signalId", saved.getId(), "version", saved.getPlanVersion(), "status", saved.getStatus());
        return Map.of("signalId", saved.getId(), "status", saved.getStatus(), "planVersion", saved.getPlanVersion());
    }

    Map<String, Object> state() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("now", now().toString());
        result.put("t", Duration.between(START.toInstant(), clock.now).toSeconds());
        result.put("price", price);
        result.put("held", held);
        result.put("sellable", sellable());
        result.put("avg", avg);
        result.put("cash", cash);
        result.put("extra", extra);
        result.put("orders", orders.stream().map(o -> Map.of("odno", o.odno, "side", o.side, "qty", o.qty, "limit", o.limit,
                "filled", o.filled, "cancelled", o.cancelled, "submittedAt", o.submittedAt)).toList());
        result.put("executions", executionRows.values().stream().map(e -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", e.getId());
            row.put("key", e.getTriggerKey());
            row.put("side", e.getOrderSide());
            row.put("status", e.getStatus());
            row.put("qty", e.getSubmittedQuantity());
            row.put("filled", e.getFilledQuantity());
            row.put("reject", e.getRejectReason());
            row.put("order", e.getOrderId());
            return row;
        }).toList());
        result.put("signals", signalRows.values().stream().map(s -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", s.getId());
            row.put("status", s.getStatus());
            row.put("version", s.getPlanVersion());
            row.put("managed", s.getManagedQuantity());
            row.put("reject", s.getRejectReason());
            row.put("stockCode", s.getStockCode());
            row.put("conditions", s.getConditionPayload());
            return row;
        }).toList());
        result.put("events", events);
        return result;
    }

    void respond(HttpExchange exchange, int status, Object body) throws IOException {
        byte[] bytes = json.writeValueAsBytes(body);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) { out.write(bytes); }
    }

    synchronized void handle(HttpExchange exchange) throws IOException {
        String path = exchange.getRequestURI().getPath();
        String raw = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        Map<String, Object> body = raw.isBlank() ? Map.of() : json.readValue(raw, new TypeReference<>() { });
        try {
            if (path.equals("/api/v1/internal/trading/signals")) {
                // As InternalTradeSignalController.save: bean validation, then the store's own rules.
                InternalTradeSignalRequest request = json.readValue(raw, InternalTradeSignalRequest.class);
                var violations = validator.validate(request);
                if (!violations.isEmpty()) {
                    respond(exchange, 400, ErrorResponse.of(ErrorCode.INVALID_REQUEST, violations.toString(), null));
                    return;
                }
                boolean deduplicated = lifecycle.hasReceipt(request.idempotencyKey());
                try {
                    TradeSignal saved = lifecycle.save(request);
                    event("publish", "stockCode", request.stockCode(), "action", request.action(), "version", request.planVersion(),
                            "signalId", saved.getId(), "status", saved.getStatus(), "deduplicated", deduplicated);
                    respond(exchange, 200, new InternalTradeSignalResponse(saved.getId(), saved.getStatus(), deduplicated));
                } catch (IllegalArgumentException | IllegalStateException ex) {
                    int status = ex instanceof IllegalStateException ? 409 : 400;
                    event("publish_refused", "stockCode", request.stockCode(), "action", request.action(), "status", status,
                            "reason", ex.getMessage());
                    respond(exchange, status, ErrorResponse.of(ErrorCode.INVALID_REQUEST, ex.getMessage(), null));
                }
            } else if (path.equals("/api/v1/internal/trading/signals/active")) {
                respond(exchange, 200, lifecycle.active(0, 200));
            } else if (path.startsWith("/api/v1/internal/trading/signals/") && path.endsWith("/trigger")) {
                String id = path.substring("/api/v1/internal/trading/signals/".length(), path.length() - "/trigger".length());
                Map<String, Object> response = lifecycle.triggerResponse(id, body);
                event("trigger", "signalId", id, "type", body.get("triggerType"), "group", body.get("groupId"),
                        "version", body.get("planVersion"), "accepted", response.get("accepted"),
                        "reason", response.get("rejectReason"), "execution", response.get("executionStatus"));
                respond(exchange, 200, response);
            } else if (path.equals("/sim/reset")) {
                reset(body);
                respond(exchange, 200, state());
            } else if (path.equals("/sim/plan")) {
                respond(exchange, 200, plan(body));
            } else if (path.equals("/sim/advance")) {
                @SuppressWarnings("unchecked") List<Number> prices = (List<Number>) body.get("prices");
                for (Number next : prices) {
                    price = next.longValue();
                    tick();
                }
                respond(exchange, 200, state());
            } else if (path.equals("/sim/price")) {
                price = ((Number) body.get("price")).longValue();
                respond(exchange, 200, state());
            } else if (path.equals("/sim/control")) {
                rateLimited = ((Number) body.getOrDefault("rateLimited", rateLimited)).intValue();
                respond(exchange, 200, state());
            } else if (path.equals("/sim/state")) {
                respond(exchange, 200, state());
            } else {
                respond(exchange, 404, Map.of("error", path));
            }
        } catch (RuntimeException ex) {
            event("server_error", "path", path, "error", ex.getClass().getSimpleName() + ": " + ex.getMessage());
            respond(exchange, 500, Map.of("error", String.valueOf(ex.getMessage())));
        }
    }

    @Test
    void protectionScenariosHoldEndToEnd() throws Exception {
        wire();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.start();
        Path root = Path.of("..").toAbsolutePath().normalize();
        Path report = Path.of("target", "paper-lifecycle-sim.txt").toAbsolutePath();
        Files.createDirectories(report.getParent());
        try {
            ProcessBuilder builder = new ProcessBuilder(System.getenv("HQA_SIM_PYTHON"),
                    root.resolve("scripts/paper_lifecycle_sim.py").toString())
                    .directory(root.toFile()).redirectErrorStream(true).redirectOutput(report.toFile());
            builder.environment().put("HQA_SIM_BASE_URL", "http://127.0.0.1:" + server.getAddress().getPort());
            builder.environment().put("OPENAI_API_KEY", "offline-disabled");
            Process process = builder.start();
            assertThat(process.waitFor(10, TimeUnit.MINUTES)).as("simulation finished").isTrue();
            List<String> failed = Files.readAllLines(report).stream()
                    .filter(line -> line.startsWith("  FAIL") || line.contains("Traceback")).toList();
            assertThat(process.exitValue()).as("see %s: %s", report, failed).isZero();
        } finally {
            server.stop(0);
        }
    }
}
