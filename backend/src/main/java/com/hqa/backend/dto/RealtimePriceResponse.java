package com.hqa.backend.dto;

import java.time.OffsetDateTime;

public record RealtimePriceResponse(
        StockInfo stock,
        int currentPrice,
        int change,
        double changeRate,
        int openPrice,
        int highPrice,
        int lowPrice,
        long volume,
        Long marketCap,
        Double per,
        Double pbr,
        OffsetDateTime timestamp
) {
}
