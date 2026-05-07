package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class RootController {

    private final HqaProperties properties;

    public RootController(HqaProperties properties) {
        this.properties = properties;
    }

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
