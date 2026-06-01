package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.dto.InternalTradeSignalResponse;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.TradeSignalService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/internal/trading/signals")
public class InternalTradeSignalController {

    private final TradeSignalService service;
    private final HqaProperties properties;

    public InternalTradeSignalController(TradeSignalService service, HqaProperties properties) {
        this.service = service;
        this.properties = properties;
    }

    @PostMapping
    public InternalTradeSignalResponse save(@Valid @RequestBody InternalTradeSignalRequest request,
                                            @RequestHeader(value = "X-HQA-Internal-Token", required = false) String token) {
        requireInternalToken(token);
        TradeSignal saved = service.saveSignal(request);
        return new InternalTradeSignalResponse(saved.getId(), saved.getStatus(), false);
    }

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
