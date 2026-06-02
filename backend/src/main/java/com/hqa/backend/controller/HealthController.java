package com.hqa.backend.controller;

import com.hqa.backend.dto.HealthResponse;
import com.hqa.backend.service.HealthService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "시스템 / Health", description = "헬스 체크 (공개)")
@RestController
public class HealthController {

    private final HealthService healthService;

    public HealthController(HealthService healthService) {
        this.healthService = healthService;
    }

    @Operation(summary = "기본 헬스 체크", description = "서비스 상태·버전·환경·AI 서버 가용 여부를 반환한다.")
    @GetMapping("/health")
    public HealthResponse health() {
        return healthService.basic();
    }

    @Operation(summary = "상세 헬스 체크", description = "의존 컴포넌트(DB·Redis·AI 서버 등)별 상태를 포함한 상세 헬스를 반환한다.")
    @GetMapping("/health/detailed")
    public Map<String, Object> detailedHealth() {
        return healthService.detailed();
    }
}
