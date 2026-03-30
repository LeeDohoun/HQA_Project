package com.hqa.backend.dto;

import java.util.List;

public record StockSearchResponse(List<StockSearchResult> results, int total) {
}
