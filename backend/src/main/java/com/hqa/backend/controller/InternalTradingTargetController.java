package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.AutoTradeTargetService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "내부 API - 자동매매 대상", description = "Python analysis scheduler가 조회하는 서버 간 API")
@RestController
@RequestMapping("/api/v1/internal/trading")
public class InternalTradingTargetController {

    private final AutoTradeTargetService service;
    private final HqaProperties properties;

    public InternalTradingTargetController(AutoTradeTargetService service, HqaProperties properties) {
        this.service = service;
        this.properties = properties;
    }

    @Operation(summary = "자동매매 분석 대상 조회",
            description = "자동매매 ON 사용자와 관심종목, 투자 프로필을 Python scheduler에 제공한다.")
    @GetMapping("/auto-trade-targets")
    public Map<String, Object> activeTargets(
            @RequestHeader(value = "X-HQA-Internal-Token", required = false) String token
    ) {
        requireInternalToken(token);
        return service.activeTargets();
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
