package com.hqa.backend.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.StrategyStatusRequest;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import com.hqa.backend.entity.StrategyStatusSnapshot;
import com.hqa.backend.entity.TradingHalt;
import com.hqa.backend.repository.StrategyBatchOrderRepository;
import com.hqa.backend.repository.StrategyBatchRepository;
import com.hqa.backend.repository.StrategyStatusSnapshotRepository;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Status snapshots published by the Python runtime and the read-only overview for the /strategies page. */
@Service
public class StrategyStatusService {
    private final StrategyStatusSnapshotRepository statuses;
    private final StrategyBatchRepository batches;
    private final StrategyBatchOrderRepository batchOrders;
    private final StrategyRiskGuard risk;
    private final StrategySessionClock sessions;
    private final ObjectMapper mapper;

    public StrategyStatusService(StrategyStatusSnapshotRepository statuses, StrategyBatchRepository batches,
            StrategyBatchOrderRepository batchOrders, StrategyRiskGuard risk, StrategySessionClock sessions, ObjectMapper mapper) {
        this.statuses = statuses;
        this.batches = batches;
        this.batchOrders = batchOrders;
        this.risk = risk;
        this.sessions = sessions;
        this.mapper = mapper;
    }

    /** Keeps only the latest status per strategy; an older generatedAt does not replace a newer one. */
    @Transactional
    public Map<String, Object> publish(StrategyStatusRequest request) {
        if (request == null || request.strategyId() == null || !request.strategyId().matches(StrategyAccountService.STRATEGY_ID_PATTERN)) {
            throw new IllegalArgumentException("INVALID_STRATEGY_ID");
        }
        if (request.generatedAt() == null) throw new IllegalArgumentException("generatedAt is required");
        if (request.payload() == null) throw new IllegalArgumentException("payload is required");
        Object declared = request.payload().get("strategyId");
        if (declared != null && !request.strategyId().equals(declared)) throw new IllegalArgumentException("STRATEGY_MISMATCH");
        StrategyStatusSnapshot snapshot = statuses.findById(request.strategyId())
                .orElseGet(() -> new StrategyStatusSnapshot(request.strategyId()));
        if (snapshot.getGeneratedAt() != null && snapshot.getGeneratedAt().isAfter(request.generatedAt())) {
            return Map.of("stored", false);
        }
        try {
            snapshot.setPayload(mapper.writeValueAsString(request.payload()));
        } catch (JsonProcessingException ex) {
            throw new IllegalArgumentException("payload is not serializable", ex);
        }
        snapshot.setGeneratedAt(request.generatedAt());
        snapshot.setUpdatedAt(sessions.now());
        statuses.save(snapshot);
        return Map.of("stored", true);
    }

    /** Database-only overview; risk is null so a page load never triggers a KIS balance inquiry. */
    @Transactional(readOnly = true)
    public Map<String, Object> overview() {
        List<Map<String, Object>> strategies = new ArrayList<>();
        for (StrategyStatusSnapshot snapshot : statuses.findAllByOrderByStrategyIdAsc()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("strategyId", snapshot.getStrategyId());
            row.put("updatedAt", snapshot.getUpdatedAt());
            row.put("payload", payload(snapshot));
            row.put("latestBatch", batches.findFirstByStrategyIdOrderByCreatedAtDesc(snapshot.getStrategyId())
                    .map(this::batchSummary).orElse(null));
            row.put("risk", null);
            strategies.add(row);
        }
        List<Map<String, Object>> halts = new ArrayList<>(risk.halts());
        if (halts.stream().noneMatch(row -> TradingHalt.GLOBAL.equals(row.get("scope")))) {
            Map<String, Object> global = new LinkedHashMap<>();
            global.put("scope", TradingHalt.GLOBAL);
            global.put("halted", false);
            global.put("reason", null);
            global.put("updatedAt", null);
            halts.add(0, global);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("strategies", strategies);
        result.put("halts", halts);
        return result;
    }

    Map<String, Object> batchSummary(StrategyBatch batch) {
        List<StrategyBatchOrder> orders = batchOrders.findByBatchIdOrderBySeqAsc(batch.getId());
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("batchId", batch.getId().toString());
        summary.put("phase", batch.getPhase());
        summary.put("sessionDate", batch.getSessionDate().toString());
        summary.put("status", batch.getStatus());
        summary.put("orderCount", orders.size());
        summary.put("filledCount", orders.stream().filter(order -> order.getFilledQuantity() > 0).count());
        summary.put("rejectedCount", orders.stream().filter(order -> "REJECTED".equals(order.getStatus())).count());
        summary.put("avgSlippagePct", averageSlippagePct(orders));
        return summary;
    }

    /** Mean of (fill - ref) / ref * 100 over filled orders, sign flipped for sells: adverse is positive. */
    public static Double averageSlippagePct(List<StrategyBatchOrder> orders) {
        double sum = 0;
        int count = 0;
        for (StrategyBatchOrder order : orders) {
            if (order.getFilledQuantity() <= 0 || order.getAverageFillPrice() == null || order.getRefPrice() <= 0) continue;
            double pct = (order.getAverageFillPrice().doubleValue() - order.getRefPrice()) / order.getRefPrice() * 100.0;
            sum += order.isBuy() ? pct : -pct;
            count++;
        }
        return count == 0 ? null : Math.round(sum / count * 10_000.0) / 10_000.0;
    }

    private Object payload(StrategyStatusSnapshot snapshot) {
        try {
            return mapper.readValue(snapshot.getPayload(), new TypeReference<Map<String, Object>>() { });
        } catch (JsonProcessingException ex) {
            return null;
        }
    }
}
