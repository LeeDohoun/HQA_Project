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
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
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
        TradeSignal saved = service.saveSignal(request);
        return new InternalTradeSignalResponse(saved.getId(), saved.getStatus(), false);
    }

    @Operation(summary = "내부 시그널 서비스 헬스", description = "내부 매매 시그널 서비스의 동작 여부를 확인한다.")
    @GetMapping("/health")
    public java.util.Map<String, Object> health() {
        return java.util.Map.of("status", "ok");
    }

    private void requireInternalToken(String token) {
        String expected = properties.getInternalToken();
        if (expected == null || expected.isBlank()) {
            return;
        }
        if (!expected.equals(token)) {
            throw new ApiException(ErrorCode.UNAUTHORIZED, 401, "Invalid internal token", null);
        }
    }
}
