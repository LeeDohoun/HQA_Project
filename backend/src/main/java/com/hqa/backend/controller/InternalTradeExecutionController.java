package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.TradeSignalService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "내부 API - 주문 확인", description = "운영자 도구(scripts/paper_orders.py)용. X-HQA-Internal-Token 헤더 필요")
@RestController
@RequestMapping("/api/v1/internal/trading/executions")
public class InternalTradeExecutionController {

    private final TradeSignalService service;
    private final HqaProperties properties;

    public InternalTradeExecutionController(TradeSignalService service, HqaProperties properties) {
        this.service = service;
        this.properties = properties;
    }

    @Operation(summary = "운영자 확인이 필요한 주문",
            description = "브로커 주문번호 없이 결과를 알 수 없는(UNKNOWN) 주문. 확인 전까지 해당 계획의 모든 트리거가 보류된다.")
    @GetMapping("/unknown")
    public Map<String, Object> unknown(@RequestHeader(value = "X-HQA-Internal-Token", required = false) String token) {
        requireInternalToken(token);
        return Map.of("executions", service.ordersAwaitingOperator());
    }

    @Operation(summary = "UNKNOWN 주문 확인 결과 기록",
            description = "KIS 주문내역으로 검증한 뒤 brokerOrderId의 주문을 연결하거나, notSubmitted로 미접수를 기록한다. note 필수.")
    @PostMapping("/{executionId}/resolution")
    public Map<String, Object> resolve(@PathVariable String executionId, @RequestBody Map<String, Object> body,
            @RequestHeader(value = "X-HQA-Internal-Token", required = false) String token) {
        requireInternalToken(token);
        Object orderId = body.get("brokerOrderId");
        Object note = body.get("note");
        return service.resolveUnknownOrder(executionId, orderId == null ? null : String.valueOf(orderId),
                Boolean.TRUE.equals(body.get("notSubmitted")), note == null ? null : String.valueOf(note));
    }

    private void requireInternalToken(String token) {
        String expected = properties.getInternalToken();
        if (expected == null || expected.isBlank() || !expected.equals(token)) {
            throw new ApiException(ErrorCode.UNAUTHORIZED, 401, "Invalid internal token", null);
        }
    }
}
