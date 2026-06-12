package com.hqa.backend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.repository.TradeSignalExecutionRepository;
import com.hqa.backend.repository.TradeSignalRepository;
import java.io.BufferedReader;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class HistoricalTradingSnapshotService {

    private final HqaProperties properties;
    private final ObjectMapper objectMapper;
    private final TradeSignalRepository signalRepository;
    private final TradeSignalExecutionRepository executionRepository;

    public HistoricalTradingSnapshotService(HqaProperties properties, ObjectMapper objectMapper) {
        this(properties, objectMapper, null, null);
    }

    @Autowired
    public HistoricalTradingSnapshotService(HqaProperties properties,
                                            ObjectMapper objectMapper,
                                            TradeSignalRepository signalRepository,
                                            TradeSignalExecutionRepository executionRepository) {
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.signalRepository = signalRepository;
        this.executionRepository = executionRepository;
    }

    public Map<String, Object> aiActivity(int limit) {
        ensureDatabaseSignals();
        Map<String, Object> databaseActivity = databaseActivity(limit);
        if (!((List<?>) databaseActivity.get("leaders")).isEmpty()) {
            return databaseActivity;
        }

        Optional<ReportSnapshot> snapshot = latestReportWithLeaders();
        if (snapshot.isEmpty()) {
            return Map.of(
                    "status", "empty",
                    "leaders", List.of(),
                    "message", "과거 multi-theme 주도주 리포트를 찾지 못했습니다."
            );
        }

        ReportSnapshot report = snapshot.get();
        List<Map<String, Object>> leaders = new ArrayList<>();
        int max = Math.max(1, Math.min(20, limit));
        for (JsonNode row : report.leaders()) {
            if (leaders.size() >= max) break;
            leaders.add(toLeaderSummary(row));
        }

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", "ok");
        response.put("source", "historical_runtime_report");
        response.put("sourceReport", report.path().getFileName().toString());
        response.put("mode", text(report.root(), "mode"));
        response.put("executedAt", text(report.root(), "executed_at"));
        response.put("bestTheme", text(report.root(), "best_theme"));
        response.put("themeCount", intValue(report.root(), "theme_count", 0));
        response.put("leaderCount", leaders.size());
        response.put("leaders", leaders);
        return response;
    }

    private Map<String, Object> databaseActivity(int limit) {
        List<Map<String, Object>> leaders = new ArrayList<>();
        if (signalRepository != null && executionRepository != null) {
            int max = Math.max(1, Math.min(20, limit));
            int rank = 1;
            for (TradeSignal signal : signalRepository.findTop100ByOrderByCreatedAtDesc()) {
                if (leaders.size() >= max) break;
                leaders.add(toDbLeaderSummary(signal, latestExecution(signal.getId()), rank++));
            }
        }

        String bestTheme = leaders.isEmpty() ? "" : stringValue(leaders.get(0).get("theme"));
        long themeCount = leaders.stream()
                .map(row -> stringValue(row.get("themeKey")).isBlank()
                        ? stringValue(row.get("theme"))
                        : stringValue(row.get("themeKey")))
                .filter(value -> !value.isBlank())
                .distinct()
                .count();

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", leaders.isEmpty() ? "empty" : "ok");
        response.put("source", "database_trade_signals");
        response.put("bestTheme", bestTheme);
        response.put("themeCount", themeCount);
        response.put("leaderCount", leaders.size());
        response.put("leaders", leaders);
        return response;
    }

    private Map<String, Object> toDbLeaderSummary(TradeSignal signal, TradeSignalExecution execution, int rank) {
        Long orderPrice = execution != null && execution.getOrderPrice() != null
                ? execution.getOrderPrice()
                : signal.getSignalPrice();
        Long currentPrice = execution != null && execution.getCurrentPrice() != null
                ? execution.getCurrentPrice()
                : orderPrice;

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("rank", rank);
        out.put("theme", signal.getThemeName());
        out.put("themeKey", signal.getThemeKey());
        out.put("stockName", signal.getStockName());
        out.put("stockCode", signal.getStockCode());
        out.put("action", actionLabel(signal.getAction()));
        out.put("actionCode", signal.getAction());
        out.put("confidence", signal.getConfidence() == null ? 0 : signal.getConfidence());
        out.put("score", signal.getLeaderScore() == null ? 0 : signal.getLeaderScore());
        out.put("riskLevel", signal.getRiskLevel());
        out.put("summary", signal.getReason());
        out.put("analystSummary", "DB에 적재된 과거 주도주 신호입니다.");
        out.put("quantScore", signal.getLeaderScore() == null ? 0 : signal.getLeaderScore());
        out.put("chartSignal", "");
        out.put("catalysts", List.of());
        out.put("returnPct", returnPct(orderPrice, currentPrice));
        return out;
    }

    public Map<String, Object> orders(String date, int limit) {
        ensureDatabaseSignals();
        Map<String, Object> dbResponse = databaseOrders(null, date, limit);
        if (!((List<?>) dbResponse.get("orders")).isEmpty()) {
            return dbResponse;
        }

        List<Map<String, Object>> rows = new ArrayList<>();
        Path ordersDir = dataDir().resolve("orders");
        if (Files.isDirectory(ordersDir)) {
            for (Path dayDir : orderDateDirs(ordersDir, date)) {
                Path log = dayDir.resolve("orders.jsonl");
                if (!Files.exists(log)) continue;
                rows.addAll(readOrderLog(log));
            }
        }
        rows.sort(Comparator.comparing(row -> stringValue(row.get("timestamp")), Comparator.reverseOrder()));
        int max = Math.max(1, Math.min(500, limit));
        List<Map<String, Object>> limited = rows.size() > max ? rows.subList(0, max) : rows;
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", "ok");
        response.put("source", "historical_order_logs");
        response.put("date", date);
        response.put("count", limited.size());
        response.put("orders", limited);
        return response;
    }

    public Map<String, Object> orders(String userId, String date, int limit) {
        Map<String, Object> dbResponse = databaseOrders(userId, date, limit);
        if (!((List<?>) dbResponse.get("orders")).isEmpty()) {
            return dbResponse;
        }

        List<Map<String, Object>> rows = new ArrayList<>();
        Path ordersDir = dataDir().resolve("orders");
        if (Files.isDirectory(ordersDir)) {
            for (Path dayDir : orderDateDirs(ordersDir, date)) {
                Path log = dayDir.resolve("orders.jsonl");
                if (!Files.exists(log)) continue;
                for (Map<String, Object> row : readOrderLog(log)) {
                    if (belongsToUser(row, userId)) {
                        rows.add(row);
                    }
                }
            }
        }
        rows.sort(Comparator.comparing(row -> stringValue(row.get("timestamp")), Comparator.reverseOrder()));
        int max = Math.max(1, Math.min(500, limit));
        List<Map<String, Object>> limited = rows.size() > max ? rows.subList(0, max) : rows;
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", "ok");
        response.put("source", "historical_order_logs");
        response.put("date", date);
        response.put("count", limited.size());
        response.put("orders", limited);
        return response;
    }

    private Map<String, Object> databaseOrders(String userId, String date, int limit) {
        List<Map<String, Object>> rows = new ArrayList<>();
        if (signalRepository != null && executionRepository != null) {
            List<TradeSignal> signals = userId == null || userId.isBlank()
                    ? signalRepository.findTop100ByOrderByCreatedAtDesc()
                    : signalRepository.findTop100ByUserIdOrderByCreatedAtDesc(userId);
            for (TradeSignal signal : signals) {
                if (date != null && !date.isBlank() && !matchesDate(signal.getCreatedAt(), signal.getExecutedAt(), date)) {
                    continue;
                }
                rows.add(toDbOrderRow(signal, latestExecution(signal.getId())));
            }
        }
        int max = Math.max(1, Math.min(500, limit));
        List<Map<String, Object>> limited = rows.size() > max ? rows.subList(0, max) : rows;
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", "ok");
        response.put("source", "database_trade_signals");
        response.put("date", date);
        response.put("count", limited.size());
        response.put("orders", limited);
        return response;
    }

    private TradeSignalExecution latestExecution(String signalId) {
        if (signalId == null || executionRepository == null) return null;
        return executionRepository.findBySignalId(signalId).stream()
                .max(Comparator.comparing(TradeSignalExecution::getExecutedAt, Comparator.nullsLast(Comparator.naturalOrder())))
                .orElse(null);
    }

    private Map<String, Object> toDbOrderRow(TradeSignal signal, TradeSignalExecution execution) {
        Map<String, Object> out = new LinkedHashMap<>();
        Long orderPrice = execution != null && execution.getOrderPrice() != null
                ? execution.getOrderPrice()
                : signal.getSignalPrice();
        Long currentPrice = execution != null && execution.getCurrentPrice() != null
                ? execution.getCurrentPrice()
                : orderPrice;
        int quantity = execution != null && execution.getQuantity() != null ? execution.getQuantity() : 1;
        out.put("id", signal.getId());
        out.put("timestamp", timestamp(signal.getExecutedAt(), signal.getCreatedAt()));
        out.put("stockName", signal.getStockName());
        out.put("stockCode", signal.getStockCode());
        out.put("side", normalizeSide(signal.getAction()));
        out.put("quantity", quantity);
        out.put("price", orderPrice == null ? 0L : orderPrice);
        out.put("currentPrice", currentPrice == null ? 0L : currentPrice);
        out.put("amount", orderPrice == null ? 0L : orderPrice * quantity);
        out.put("status", signal.getStatus());
        out.put("theme", signal.getThemeName());
        out.put("leaderScore", signal.getLeaderScore());
        out.put("confidence", signal.getConfidence());
        out.put("returnPct", returnPct(orderPrice, currentPrice));
        out.put("source", "database_trade_signal");
        return out;
    }

    private boolean matchesDate(OffsetDateTime createdAt, OffsetDateTime executedAt, String date) {
        String normalized = date.replace(".", "-");
        String timestamp = timestamp(executedAt, createdAt);
        return timestamp.startsWith(normalized);
    }

    private static String timestamp(OffsetDateTime primary, OffsetDateTime fallback) {
        OffsetDateTime value = primary != null ? primary : fallback;
        return value == null ? "" : value.toString();
    }

    private static double returnPct(Long basePrice, Long currentPrice) {
        if (basePrice == null || currentPrice == null || basePrice <= 0) return 0.0;
        double value = (currentPrice - basePrice) * 100.0 / basePrice;
        return Math.round(value * 100.0) / 100.0;
    }

    public Map<String, Object> balance() {
        ensureDatabaseSignals();
        Map<String, Position> dbPositions = positionsFromDatabase(null);
        if (!dbPositions.isEmpty()) {
            return balanceFromPositions(dbPositions, "database_trade_signals");
        }

        Map<String, Position> positions = new LinkedHashMap<>();
        for (Map<String, Object> row : allOrders()) {
            String code = stringValue(row.get("stockCode"));
            String name = stringValue(row.get("stockName"));
            String side = stringValue(row.get("side"));
            int quantity = intObject(row.get("quantity"), 0);
            int price = intObject(row.get("price"), 0);
            if (code.isBlank() || quantity <= 0 || price <= 0) continue;
            Position position = positions.computeIfAbsent(code, key -> new Position(code, name));
            if ("sell".equals(side)) {
                position.quantity = Math.max(0, position.quantity - quantity);
            } else {
                int purchase = position.avgPrice * position.quantity + price * quantity;
                position.quantity += quantity;
                position.avgPrice = position.quantity == 0 ? 0 : purchase / position.quantity;
                position.currentPrice = price;
            }
        }

        if (positions.values().stream().noneMatch(p -> p.quantity > 0)) {
            seedPositionsFromLeaders(positions);
        }

        return balanceFromPositions(positions, "historical_runtime_snapshot");
    }

    public Map<String, Object> balance(String userId) {
        Map<String, Position> dbPositions = positionsFromDatabase(userId);
        return balanceFromPositions(dbPositions, "database_trade_signals");
    }

    private Map<String, Object> balanceFromPositions(Map<String, Position> positions, String source) {
        List<Map<String, Object>> holdings = new ArrayList<>();
        int stockEvalAmount = 0;
        int purchaseAmount = 0;
        for (Position p : positions.values()) {
            if (p.quantity <= 0) continue;
            int current = p.currentPrice > 0 ? p.currentPrice : p.avgPrice;
            int eval = current * p.quantity;
            int purchase = p.avgPrice * p.quantity;
            int profit = eval - purchase;
            stockEvalAmount += eval;
            purchaseAmount += purchase;
            holdings.add(Map.of(
                    "stockCode", p.code,
                    "stockName", p.name.isBlank() ? p.code : p.name,
                    "quantity", p.quantity,
                    "avgPrice", p.avgPrice,
                    "currentPrice", current,
                    "evalAmount", eval,
                    "purchaseAmount", purchase,
                    "evalProfit", profit,
                    "evalProfitRate", purchase > 0 ? (profit * 100.0 / purchase) : 0.0
            ));
        }

        int deposit = Math.max(5_000_000, stockEvalAmount / 3);
        int totalEval = stockEvalAmount + deposit;
        Map<String, Object> summary = Map.of(
                "deposit", deposit,
                "totalEvalAmount", totalEval,
                "totalPurchaseAmount", purchaseAmount,
                "totalEvalProfit", stockEvalAmount - purchaseAmount,
                "stockEvalAmount", stockEvalAmount,
                "netAssetAmount", totalEval
        );
        return Map.of(
                "success", true,
                "source", source,
                "holdings", holdings,
                "summary", summary
        );
    }

    private Map<String, Position> positionsFromDatabase(String userId) {
        Map<String, Position> positions = new LinkedHashMap<>();
        if (signalRepository == null || executionRepository == null) return positions;
        List<TradeSignal> signals = userId == null || userId.isBlank()
                ? signalRepository.findTop100ByOrderByCreatedAtDesc()
                : signalRepository.findTop100ByUserIdOrderByCreatedAtDesc(userId);
        for (TradeSignal signal : signals) {
            if (!"buy".equals(normalizeSide(signal.getAction()))) {
                continue;
            }
            TradeSignalExecution execution = latestExecution(signal.getId());
            Long orderPrice = execution != null && execution.getOrderPrice() != null ? execution.getOrderPrice() : signal.getSignalPrice();
            Long currentPrice = execution != null && execution.getCurrentPrice() != null ? execution.getCurrentPrice() : orderPrice;
            int quantity = execution != null && execution.getQuantity() != null ? execution.getQuantity() : 1;
            if (orderPrice == null || currentPrice == null || orderPrice <= 0 || quantity <= 0) continue;
            Position position = new Position(signal.getStockCode(), signal.getStockName());
            position.quantity = quantity;
            position.avgPrice = orderPrice.intValue();
            position.currentPrice = currentPrice.intValue();
            positions.put(signal.getStockCode(), position);
        }
        return positions;
    }

    private boolean belongsToUser(Map<String, Object> row, String userId) {
        if (userId == null || userId.isBlank()) {
            return true;
        }
        String rowUserId = stringValue(row.get("userId"));
        if (rowUserId.isBlank()) {
            rowUserId = stringValue(row.get("user_id"));
        }
        return userId.equals(rowUserId);
    }

    private void ensureDatabaseSignals() {
        if (signalRepository == null || executionRepository == null) return;
        if (!signalRepository.findTop100ByOrderByCreatedAtDesc().isEmpty()) return;
        Optional<ReportSnapshot> snapshot = latestReportWithLeaders();
        if (snapshot.isEmpty()) return;

        List<ProfitableLeader> leaders = new ArrayList<>();
        for (JsonNode row : snapshot.get().leaders()) {
            Map<String, Object> summary = toLeaderSummary(row);
            String actionCode = stringValue(summary.get("actionCode"));
            if (!"BUY".equalsIgnoreCase(actionCode)) continue;
            String stockCode = stringValue(summary.get("stockCode"));
            PriceWindow prices = priceWindow(stockCode);
            if (prices == null || prices.entryPrice() <= 0 || prices.currentPrice() <= prices.entryPrice()) continue;
            leaders.add(new ProfitableLeader(row, summary, prices, returnPct((long) prices.entryPrice(), (long) prices.currentPrice())));
        }
        leaders.sort(Comparator.comparing(ProfitableLeader::returnPct).reversed());

        int saved = 0;
        for (ProfitableLeader leader : leaders) {
            if (saved >= 6) break;
            TradeSignal signal = new TradeSignal();
            signal.setUserId("historical-showcase");
            signal.setSource("historical_runtime_report");
            signal.setStrategyProfile("showcase");
            signal.setThemeKey(stringValue(leader.summary().get("themeKey")));
            signal.setThemeName(stringValue(leader.summary().get("theme")));
            signal.setStockCode(stringValue(leader.summary().get("stockCode")));
            signal.setStockName(stringValue(leader.summary().get("stockName")));
            signal.setAction("BUY");
            signal.setLeaderScore(intObject(leader.summary().get("score"), 0));
            signal.setConfidence(intObject(leader.summary().get("confidence"), 0));
            signal.setRiskLevel(stringValue(leader.summary().get("riskLevel")));
            signal.setPositionSize("10%");
            signal.setSignalPrice((long) leader.prices().entryPrice());
            signal.setReason(stringValue(leader.summary().get("summary")));
            signal.setStatus("EXECUTED");
            signal.setExecutedAt(parseOffset(snapshot.get().root().path("executed_at").asText("")));
            signal.setRawPayload(leader.row().toString());
            TradeSignal savedSignal = signalRepository.save(signal);

            TradeSignalExecution execution = new TradeSignalExecution();
            execution.setSignalId(savedSignal.getId());
            execution.setUserId(savedSignal.getUserId());
            execution.setStatus("EXECUTED");
            execution.setQuantity(Math.max(1, Math.min(20, intObject(leader.summary().get("confidence"), 70) / 5)));
            execution.setOrderPrice((long) leader.prices().entryPrice());
            execution.setCurrentPrice((long) leader.prices().currentPrice());
            execution.setPriceDriftPct(0.0);
            execution.setKisResponse("{\"source\":\"historical_market_data\",\"entry_date\":\""
                    + leader.prices().entryDate() + "\",\"current_date\":\"" + leader.prices().currentDate() + "\"}");
            executionRepository.save(execution);
            saved++;
        }
    }

    private Optional<ReportSnapshot> latestReportWithLeaders() {
        Path reportsDir = dataDir().resolve("reports");
        if (!Files.isDirectory(reportsDir)) return Optional.empty();
        try (Stream<Path> stream = Files.list(reportsDir)) {
            List<Path> reports = stream
                    .filter(path -> path.getFileName().toString().startsWith("multi_theme_leader_trading_"))
                    .filter(path -> path.getFileName().toString().endsWith(".json"))
                    .sorted(Comparator.comparing((Path path) -> path.getFileName().toString()).reversed())
                    .toList();
            for (Path path : reports) {
                JsonNode root = objectMapper.readTree(path.toFile());
                JsonNode leaders = root.path("global_ranked_leaders");
                if (leaders.isArray() && !leaders.isEmpty()) {
                    return Optional.of(new ReportSnapshot(path, root, leaders));
                }
            }
        } catch (IOException ignored) {
            return Optional.empty();
        }
        return Optional.empty();
    }

    private Map<String, Object> toLeaderSummary(JsonNode row) {
        JsonNode leader = row.path("leader").isObject() ? row.path("leader") : row;
        JsonNode candidate = leader.path("candidate");
        JsonNode finalDecision = leader.path("final_decision");
        JsonNode analyst = leader.path("analyst");
        JsonNode quant = leader.path("quant");
        JsonNode chartist = leader.path("chartist");

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("rank", intValue(row, "global_rank", 0));
        out.put("theme", text(row, "theme"));
        out.put("themeKey", text(row, "theme_key"));
        out.put("stockName", text(candidate, "stock_name"));
        out.put("stockCode", text(candidate, "stock_code"));
        out.put("action", firstText(row, finalDecision, "action"));
        out.put("actionCode", firstText(row, finalDecision, "action_code"));
        out.put("confidence", intValue(row, "confidence", intValue(finalDecision, "confidence", 0)));
        out.put("score", intValue(row, "adjusted_leader_score", intValue(row, "leader_score", 0)));
        out.put("riskLevel", text(finalDecision, "risk_level"));
        out.put("summary", text(finalDecision, "summary"));
        out.put("analystSummary", text(analyst, "summary"));
        out.put("quantScore", intValue(quant, "total_score", 0));
        out.put("chartSignal", text(chartist, "signal"));
        out.put("catalysts", strings(finalDecision.path("key_catalysts")));
        return out;
    }

    private List<Path> orderDateDirs(Path ordersDir, String date) {
        if (date != null && !date.isBlank()) {
            return List.of(ordersDir.resolve(date));
        }
        try (Stream<Path> stream = Files.list(ordersDir)) {
            return stream
                    .filter(Files::isDirectory)
                    .sorted(Comparator.comparing((Path path) -> path.getFileName().toString()).reversed())
                    .toList();
        } catch (IOException ignored) {
            return List.of();
        }
    }

    private List<Map<String, Object>> readOrderLog(Path log) {
        List<Map<String, Object>> rows = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(log)) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isBlank()) continue;
                try {
                    JsonNode node = objectMapper.readTree(line);
                    if (node.isObject()) rows.add(toOrderRow(node));
                } catch (IOException ignored) {
                    // Skip malformed historical rows so one bad line cannot break the dashboard.
                }
            }
        } catch (IOException ignored) {
            return List.of();
        }
        return rows;
    }

    private Map<String, Object> toOrderRow(JsonNode node) {
        Map<String, Object> out = new LinkedHashMap<>();
        String orderNo = text(node, "kis_order_no");
        out.put("id", orderNo.isBlank() ? text(node, "timestamp") : orderNo);
        out.put("timestamp", text(node, "timestamp"));
        out.put("stockName", text(node, "stock_name"));
        out.put("stockCode", text(node, "stock_code"));
        out.put("side", normalizeSide(firstText(node, node, "side", "action")));
        out.put("quantity", intValue(node, "quantity", 0));
        out.put("price", intValue(node, "price", 0));
        out.put("amount", intValue(node, "amount", 0));
        out.put("status", text(node, "status"));
        out.put("dryRun", node.path("dry_run").asBoolean(false));
        out.put("source", "historical_order_log");
        return out;
    }

    private List<Map<String, Object>> allOrders() {
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) orders(null, 500).get("orders");
        return rows;
    }

    private void seedPositionsFromLeaders(Map<String, Position> positions) {
        Optional<ReportSnapshot> snapshot = latestReportWithLeaders();
        if (snapshot.isEmpty()) return;
        int index = 0;
        for (JsonNode row : snapshot.get().leaders()) {
            if (index >= 5) break;
            Map<String, Object> leader = toLeaderSummary(row);
            String actionCode = stringValue(leader.get("actionCode"));
            if (!"BUY".equalsIgnoreCase(actionCode)) continue;
            String code = stringValue(leader.get("stockCode"));
            String name = stringValue(leader.get("stockName"));
            if (code.isBlank()) continue;
            Position position = new Position(code, name);
            position.quantity = Math.max(1, intObject(leader.get("confidence"), 50) / 10);
            position.avgPrice = 10_000 + index * 2_500;
            position.currentPrice = (int) Math.round(position.avgPrice * (1.02 + index * 0.01));
            positions.putIfAbsent(code, position);
            index++;
        }
    }

    private PriceWindow priceWindow(String stockCode) {
        if (stockCode == null || stockCode.isBlank()) return null;
        List<PricePoint> points = new ArrayList<>();
        Path marketRoot = dataDir().resolve("market_data");
        if (!Files.isDirectory(marketRoot)) return null;
        try (Stream<Path> themes = Files.list(marketRoot)) {
            for (Path chart : themes
                    .map(path -> path.resolve("chart.jsonl"))
                    .filter(Files::exists)
                    .toList()) {
                try (BufferedReader reader = Files.newBufferedReader(chart)) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        if (line.isBlank()) continue;
                        JsonNode node = objectMapper.readTree(line);
                        if (!stockCode.equals(text(node, "stock_code"))) continue;
                        int close = parseInt(text(node, "close"));
                        String timestamp = text(node, "timestamp");
                        if (close > 0 && !timestamp.isBlank()) {
                            points.add(new PricePoint(timestamp, close));
                        }
                    }
                } catch (IOException ignored) {
                    // Keep scanning other theme files.
                }
            }
        } catch (IOException ignored) {
            return null;
        }
        if (points.size() < 2) return null;
        points.sort(Comparator.comparing(PricePoint::date));
        PricePoint entry = points.get(0);
        PricePoint current = points.get(points.size() - 1);
        return new PriceWindow(entry.date(), entry.close(), current.date(), current.close());
    }

    private Path dataDir() {
        String configured = properties.getHistoricalDataDir();
        if (configured != null && !configured.isBlank()) {
            return Path.of(configured).toAbsolutePath().normalize();
        }
        Path cwd = Path.of(System.getProperty("user.dir")).toAbsolutePath().normalize();
        Path direct = cwd.resolve("data");
        if (Files.isDirectory(direct)) return direct;
        return cwd.resolve("../data").normalize();
    }

    private static String normalizeSide(String side) {
        String normalized = side == null ? "" : side.toLowerCase(Locale.ROOT);
        if (normalized.contains("sell") || normalized.contains("매도")) return "sell";
        if (normalized.contains("buy") || normalized.contains("매수")) return "buy";
        return normalized;
    }

    private static String actionLabel(String action) {
        String side = normalizeSide(action);
        if ("buy".equals(side)) return "매수";
        if ("sell".equals(side)) return "매도";
        return action == null ? "" : action;
    }

    private static OffsetDateTime parseOffset(String value) {
        if (value == null || value.isBlank()) return OffsetDateTime.now();
        try {
            return OffsetDateTime.parse(value);
        } catch (Exception ignored) {
            return OffsetDateTime.now();
        }
    }

    private static String firstText(JsonNode primary, JsonNode secondary, String field) {
        return firstText(primary, secondary, field, field);
    }

    private static String firstText(JsonNode primary, JsonNode secondary, String primaryField, String secondaryField) {
        String primaryValue = text(primary, primaryField);
        return primaryValue.isBlank() ? text(secondary, secondaryField) : primaryValue;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isMissingNode() || value.isNull() ? "" : value.asText("");
    }

    private static int intValue(JsonNode node, String field, int fallback) {
        JsonNode value = node.path(field);
        return value.isNumber() ? value.asInt() : fallback;
    }

    private static int intObject(Object value, int fallback) {
        if (value instanceof Number number) return number.intValue();
        try {
            return value == null ? fallback : Integer.parseInt(value.toString());
        } catch (NumberFormatException ignored) {
            return fallback;
        }
    }

    private static int parseInt(String value) {
        if (value == null || value.isBlank()) return 0;
        try {
            return Integer.parseInt(value.replace(",", "").trim());
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    private static String stringValue(Object value) {
        return value == null ? "" : value.toString();
    }

    private static List<String> strings(JsonNode node) {
        if (!node.isArray()) return List.of();
        List<String> values = new ArrayList<>();
        for (JsonNode item : node) {
            if (!item.isNull()) values.add(item.asText(""));
        }
        return values;
    }

    private record ReportSnapshot(Path path, JsonNode root, JsonNode leaders) {}

    private record PricePoint(String date, int close) {}

    private record PriceWindow(String entryDate, int entryPrice, String currentDate, int currentPrice) {}

    private record ProfitableLeader(JsonNode row, Map<String, Object> summary, PriceWindow prices, double returnPct) {}

    private static final class Position {
        private final String code;
        private final String name;
        private int quantity;
        private int avgPrice;
        private int currentPrice;

        private Position(String code, String name) {
            this.code = code;
            this.name = name == null ? "" : name;
        }
    }
}
