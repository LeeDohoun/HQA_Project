package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.StrategyAccountRequest;
import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.dto.StrategyNavCloseRequest;
import com.hqa.backend.dto.StrategyOrderResolutionRequest;
import com.hqa.backend.dto.StrategyStatusRequest;
import com.hqa.backend.dto.TradingHaltRequest;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.StrategyAccountService;
import com.hqa.backend.service.StrategyBatchService;
import com.hqa.backend.service.StrategyOrderResolver;
import com.hqa.backend.service.StrategyRiskGuard;
import com.hqa.backend.service.StrategyStatusService;
import com.hqa.backend.service.StrategyTerminationService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "내부 API - 전략 실행", description = "전략 계좌·일괄 주문·위험 한도·중단 스위치. X-HQA-Internal-Token 헤더 필요")
@RestController
@RequestMapping("/api/v1/internal/strategies")
public class InternalStrategyController {
    private static final String TOKEN = "X-HQA-Internal-Token";
    private final StrategyBatchService batches;
    private final StrategyAccountService accounts;
    private final StrategyRiskGuard risk;
    private final StrategyStatusService statuses;
    private final HqaProperties properties;
    private final StrategyOrderResolver resolver;
    private final StrategyTerminationService termination;

    public InternalStrategyController(StrategyBatchService batches, StrategyAccountService accounts, StrategyRiskGuard risk,
            StrategyStatusService statuses, StrategyOrderResolver resolver, StrategyTerminationService termination,
            HqaProperties properties) {
        this.termination = termination;
        this.resolver = resolver;
        this.batches = batches;
        this.accounts = accounts;
        this.risk = risk;
        this.statuses = statuses;
        this.properties = properties;
    }

    @Operation(summary = "전략 일괄 주문 접수", description = "백엔드 위험 한도와 시간 창을 검사한 뒤 QUEUED로 저장한다. dryRun은 검증만 한다.")
    @PostMapping("/batches")
    public Map<String, Object> createBatch(@RequestBody StrategyBatchRequest request,
            @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        return batches.create(request);
    }

    @Operation(summary = "전략 일괄 주문 조회")
    @GetMapping("/batches/{batchId}")
    public Map<String, Object> batch(@PathVariable String batchId, @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        UUID id;
        try {
            id = UUID.fromString(batchId);
        } catch (IllegalArgumentException ex) {
            throw new IllegalArgumentException("INVALID_BATCH_ID");
        }
        try {
            return batches.describe(id);
        } catch (IllegalArgumentException ex) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 404, "BATCH_NOT_FOUND", null);
        }
    }

    @Operation(summary = "전략 상태 게시", description = "전략별 최신 상태 하나만 보관한다.")
    @PostMapping("/status")
    public Map<String, Object> publishStatus(@RequestBody StrategyStatusRequest request,
            @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        return statuses.publish(request);
    }

    @Operation(summary = "전략 계좌 목록")
    @GetMapping("/accounts")
    public Map<String, Object> accounts(@RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        return accounts.list();
    }

    @Operation(summary = "전략 계좌 연결", description = "PAPER KIS 계좌가 연결된 사용자만. 한 사용자에 전략 하나, 한 전략에 사용자 하나.")
    @PostMapping("/accounts")
    public Map<String, Object> bind(@RequestBody StrategyAccountRequest request,
            @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        if (request == null) throw new IllegalArgumentException("request body is required");
        return accounts.bind(request.userId(), request.strategyId(), request.stage(), request.enabled());
    }

    @Operation(summary = "전략 계좌 연결 해제", description = "진행 중인 일괄 주문이 없을 때만.")
    @DeleteMapping("/accounts/{userId}")
    public Map<String, Object> unbind(@PathVariable String userId, @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        return accounts.unbind(userId);
    }

    @Operation(summary = "확정 종가 순자산 게시",
            description = "종료 규칙(termination-drawdown-pct)이 있는 전략 계좌만. 세션별 1건, 같은 내용은 같은 결과, 다른 내용은 409 NAV_CLOSE_CONFLICT.")
    @PostMapping("/accounts/{userId}/nav-close")
    public Map<String, Object> navClose(@PathVariable String userId, @RequestBody StrategyNavCloseRequest request,
            @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        if (request == null) throw new IllegalArgumentException("request body is required");
        return termination.recordOfficialClose(userId, request.sessionDate(), request.navKrw(), request.cashKrw(),
                request.holdingsValueKrw(), request.source(), request.inputHash());
    }

    @Operation(summary = "중단 스위치", description = "scope는 GLOBAL 또는 userId. 켜지면 새 일괄 주문 접수와 미전송 주문 전송이 멈춘다.")
    @PostMapping("/halt")
    public Map<String, Object> halt(@RequestBody TradingHaltRequest request,
            @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        if (request == null) throw new IllegalArgumentException("request body is required");
        return risk.setHalt(request.scope(), request.halted(), request.reason());
    }

    @Operation(summary = "전략 계좌 위험 상태", description = "KIS PAPER 잔고를 새로 읽고 계좌 고점을 갱신한다.")
    @GetMapping("/risk/{userId}")
    public Map<String, Object> risk(@PathVariable String userId, @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        if (!accounts.isStrategyAccount(userId)) throw new ApiException(ErrorCode.INVALID_REQUEST, 404, "STRATEGY_ACCOUNT_NOT_FOUND", null);
        return risk.evaluate(userId).toMap();
    }

    @Operation(summary = "확인 필요 주문 목록", description = "접수 여부가 불확실한 UNKNOWN 전략 주문. 다시 보내지 않는다.")
    @GetMapping("/orders/unknown")
    public Map<String, Object> unknownOrders(@RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        return resolver.unknownOrders();
    }

    @Operation(summary = "확인 필요 주문 처리",
            description = "action=adopt(brokerOrderId, KIS 주문 내역과 종목·방향·수량 일치) 또는 not-submitted(KIS에 해당할 수 있는 주문이 없을 때만).")
    @PostMapping("/orders/{orderId}/resolution")
    public Map<String, Object> resolveOrder(@PathVariable String orderId, @RequestBody StrategyOrderResolutionRequest request,
            @RequestHeader(value = TOKEN, required = false) String token) {
        requireInternalToken(token);
        if (request == null) throw new IllegalArgumentException("request body is required");
        UUID id;
        try {
            id = UUID.fromString(orderId);
        } catch (IllegalArgumentException ex) {
            throw new IllegalArgumentException("INVALID_ORDER_ID");
        }
        return resolver.resolve(id, request.action(), request.brokerOrderId(), request.note());
    }

    private void requireInternalToken(String token) {
        String expected = properties.getInternalToken();
        if (expected == null || expected.isBlank() || !expected.equals(token)) {
            throw new ApiException(ErrorCode.UNAUTHORIZED, 401, "Invalid internal token", null);
        }
    }
}
