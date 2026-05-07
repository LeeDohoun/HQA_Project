package com.hqa.backend.dto;

import java.util.List;

public record CandleHistoryResponse(
        String stockCode,
        String timeframe,
        List<CandleData> candles,
        boolean hasMore
) {
}
