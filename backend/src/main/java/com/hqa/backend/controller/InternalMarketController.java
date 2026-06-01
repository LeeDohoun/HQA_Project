package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.InternalPriceSnapshotRequest;
import com.hqa.backend.dto.InternalPriceSnapshotResponse;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.PriceSnapshotService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/internal/market")
public class InternalMarketController {

    private final PriceSnapshotService service;
    private final HqaProperties properties;

    public InternalMarketController(PriceSnapshotService service, HqaProperties properties) {
        this.service = service;
        this.properties = properties;
    }

    @PostMapping("/price-snapshots")
    public InternalPriceSnapshotResponse priceSnapshots(
            @Valid @RequestBody InternalPriceSnapshotRequest request,
            @RequestHeader(value = "X-HQA-Internal-Token", required = false) String token
    ) {
        requireInternalToken(token);
        return new InternalPriceSnapshotResponse(
                service.getSnapshots(request.userId(), request.stockCodes())
        );
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
