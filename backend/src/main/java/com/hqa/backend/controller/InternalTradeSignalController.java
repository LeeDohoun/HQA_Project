package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.dto.InternalTradeSignalResponse;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.TradeSignalService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "내부 API - 매매 시그널", description = "서버 간 호출용. X-HQA-Internal-Token 헤더 필요")
@RestController
@RequestMapping("/api/v1/internal/trading/signals")
public class InternalTradeSignalController {

    private final TradeSignalService service;
    private final HqaProperties properties;

    public InternalTradeSignalController(TradeSignalService service, HqaProperties properties) {
        this.service = service;
        this.properties = properties;
    }

    @Operation(summary = "매매 시그널 저장",
            description = "외부/전략 서버가 생성한 매매 시그널을 저장한다. (서버 간 호출, 내부 토큰 필요)")
    @PostMapping
    public InternalTradeSignalResponse save(@Valid @RequestBody InternalTradeSignalRequest request,
                                            @RequestHeader(value = "X-HQA-Internal-Token", required = false) String token) {
        requireInternalToken(token);
        boolean deduplicated = service.hasSignalWithIdempotencyKey(request.idempotencyKey());
        TradeSignal saved = service.saveSignal(request);
        return new InternalTradeSignalResponse(saved.getId(), saved.getStatus(), deduplicated);
    }

    @Operation(summary = "모니터링 대상 매매 시그널 조회",
            description = "SignalMonitor가 조건 평가에 필요한 대기/보유 시그널을 조회한다.")
    @GetMapping("/active")
    public Map<String, Object> active(@RequestHeader(value = "X-HQA-Internal-Token", required = false) String token,
            @RequestParam(defaultValue = "0") int page, @RequestParam(defaultValue = "200") int size) {
        requireInternalToken(token);
        return service.activeSignalsForMonitor(page, size);
    }

    @Operation(summary = "조건 충족 시그널 트리거",
            description = "SignalMonitor가 조건 충족 사실만 전달하고, 백엔드가 최종 주문 게이트를 수행한다.")
    @PostMapping("/{signalId}/trigger")
    public Map<String, Object> trigger(@PathVariable String signalId,
                                               @RequestBody(required = false) Map<String, Object> triggerPayload,
                                               @RequestHeader(value = "X-HQA-Internal-Token", required = false) String token) {
        requireInternalToken(token);
        return service.triggerResponse(signalId, triggerPayload == null ? Map.of() : triggerPayload);
    }

    @Operation(summary = "내부 시그널 서비스 헬스", description = "내부 매매 시그널 서비스의 동작 여부를 확인한다.")
    @GetMapping("/health")
    public java.util.Map<String, Object> health() {
        return java.util.Map.of("status", "ok");
    }

    private void requireInternalToken(String token) {
        String expected = properties.getInternalToken();
        if (expected == null || expected.isBlank() || !expected.equals(token)) {
            throw new ApiException(ErrorCode.UNAUTHORIZED, 401, "Invalid internal token", null);
        }
    }
}
