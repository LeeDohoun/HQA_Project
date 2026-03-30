package com.hqa.backend.service;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.CandleData;
import com.hqa.backend.dto.CandleHistoryResponse;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.exception.ApiException;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class ChartService {

    private static final Map<String, Integer> TIMEFRAMES = Map.of(
            "1m", 60,
            "3m", 180,
            "5m", 300,
            "10m", 600,
            "15m", 900,
            "30m", 1800,
            "45m", 2700,
            "1h", 3600
    );

    private final HqaProperties properties;

    public ChartService(HqaProperties properties) {
        this.properties = properties;
    }

    public CandleHistoryResponse getHistoricalCandles(String stockCode, String timeframe, int count, Long before) {
        if (!stockCode.matches("^\\d{6}$")) {
            throw new ApiException(ErrorCode.STOCK_INVALID_CODE, 400, "Stock code must be 6 digits", null);
        }
        if (!TIMEFRAMES.containsKey(timeframe)) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "Unsupported timeframe", timeframe);
        }
        if (properties.getKiwoomAppKey().isBlank() || properties.getKiwoomAppSecret().isBlank()) {
            throw new ApiException(ErrorCode.CHART_API_NOT_CONFIGURED, 503, "Chart API is not configured", null);
        }
        return new CandleHistoryResponse(stockCode, timeframe, List.<CandleData>of(), false);
    }
}
