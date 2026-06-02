package com.hqa.backend.controller;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.InternalPriceSnapshotRequest;
import com.hqa.backend.dto.InternalPriceSnapshotResponse;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.PriceSnapshotService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "내부 API - 시장", description = "서버 간 호출용. X-HQA-Internal-Token 헤더 필요")
@RestController
@RequestMapping("/api/v1/internal/market")
public class InternalMarketController {

    private final PriceSnapshotService service;
    private final HqaProperties properties;

    public InternalMarketController(PriceSnapshotService service, HqaProperties properties) {
        this.service = service;
        this.properties = properties;
    }

    @Operation(summary = "다종목 가격 스냅샷",
            description = "여러 종목의 현재 가격 스냅샷을 일괄 조회한다. (서버 간 호출, 내부 토큰 필요)")
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
