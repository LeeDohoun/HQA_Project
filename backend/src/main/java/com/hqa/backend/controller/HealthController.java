package com.hqa.backend.controller;

import com.hqa.backend.dto.HealthResponse;
import com.hqa.backend.service.HealthService;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class HealthController {

    private final HealthService healthService;

    public HealthController(HealthService healthService) {
        this.healthService = healthService;
    }

    @GetMapping("/health")
    public HealthResponse health() {
        return healthService.basic();
    }

    @GetMapping("/health/detailed")
    public Map<String, Object> detailedHealth() {
        return healthService.detailed();
    }
}
