package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import java.util.List;

public record InternalPriceSnapshotRequest(
        @NotBlank String userId,
        @NotEmpty List<@NotBlank String> stockCodes
) {
}
