package com.hqa.backend.dto;

import java.util.List;

public record InternalPriceSnapshotResponse(
        List<PriceSnapshotResponse> snapshots
) {
}
