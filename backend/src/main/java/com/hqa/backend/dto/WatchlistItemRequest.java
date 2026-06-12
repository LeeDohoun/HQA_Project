package com.hqa.backend.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record WatchlistItemRequest(
        @NotBlank
        @JsonAlias({"stockName", "stock_name", "name"})
        String stockName,
        @NotBlank
        @Pattern(regexp = "^\\d{6}$")
        @JsonAlias({"stockCode", "stock_code", "code"})
        String stockCode,
        @JsonAlias({"market"})
        String market
) {
}
