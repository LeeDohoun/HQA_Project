package com.hqa.backend.dto;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import java.time.LocalDate;
import java.util.List;

@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record InternalMinuteCandleResponse(
        String stockCode,
        @JsonFormat(shape = JsonFormat.Shape.STRING, pattern = "yyyy-MM-dd") LocalDate date,
        String status,
        List<MinuteCandle> candles,
        String source,
        int pages
) {
    public record MinuteCandle(long time, double open, double high, double low, double close, long volume) {
        public static MinuteCandle from(CandleData candle) {
            return new MinuteCandle(candle.time(), candle.open(), candle.high(), candle.low(), candle.close(), candle.volume());
        }
    }
}
