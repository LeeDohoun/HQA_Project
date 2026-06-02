package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "시스템 / Health", description = "헬스 체크 (공개)")
@RestController
public class RootController {

    private final HqaProperties properties;

    public RootController(HqaProperties properties) {
        this.properties = properties;
    }

    @Operation(summary = "서비스 루트 정보", description = "서비스명·버전·문서/헬스 경로 등 기본 정보를 반환한다.")
    @GetMapping("/")
    public Map<String, String> root() {
        return Map.of(
                "service", "HQA API",
                "version", properties.getAppVersion(),
                "docs", "/actuator",
                "health", "/health"
        );
    }
}
