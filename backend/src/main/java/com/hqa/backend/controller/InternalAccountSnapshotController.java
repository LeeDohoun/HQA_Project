package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.PaperAccountSnapshotService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/internal/trading/account-snapshots")
public class InternalAccountSnapshotController {
    @com.fasterxml.jackson.databind.annotation.JsonNaming(com.fasterxml.jackson.databind.PropertyNamingStrategies.LowerCamelCaseStrategy.class)
    public record Request(@NotEmpty @Size(max = 10) List<@NotBlank String> userIds) { }
    private final PaperAccountSnapshotService service;
    private final HqaProperties properties;

    public InternalAccountSnapshotController(PaperAccountSnapshotService service, HqaProperties properties) {
        this.service = service;
        this.properties = properties;
    }

    @PostMapping
    public Map<String, Object> snapshots(@Valid @RequestBody Request request,
            @RequestHeader(value = "X-HQA-Internal-Token", required = false) String token) {
        String expected = properties.getInternalToken();
        if (expected == null || expected.isBlank() || !expected.equals(token)) {
            throw new ApiException(ErrorCode.UNAUTHORIZED, 401, "Internal token is required", null);
        }
        return Map.of("snapshots", request.userIds().stream().distinct().map(service::snapshot).toList());
    }
}
