package com.hqa.backend.dto;

public record RecommendItem(
        String stockCode,
        String stockName,
        int quantity,
        long limitPrice
) {}
