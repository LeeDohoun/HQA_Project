package com.hqa.backend.dto;

public record CandleData(
        long time,
        double open,
        double high,
        double low,
        double close,
        long volume,
        Boolean complete
) {
}
