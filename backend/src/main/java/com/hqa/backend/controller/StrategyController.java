package com.hqa.backend.controller;

import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.StrategyStatusService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpSession;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "전략", description = "전략 현황 (로그인 필요, 읽기 전용)")
@RestController
@RequestMapping("/api/v1/strategies")
public class StrategyController {
    private final AuthService authService;
    private final StrategyStatusService statuses;

    public StrategyController(AuthService authService, StrategyStatusService statuses) {
        this.authService = authService;
        this.statuses = statuses;
    }

    @Operation(summary = "전략 현황", description = "게시된 전략 상태, 최근 일괄 주문 요약, 중단 스위치. 화면 조회는 KIS를 호출하지 않는다.")
    @GetMapping
    public Map<String, Object> strategies(HttpSession session) {
        authService.requireUser(session);
        return statuses.overview();
    }
}
