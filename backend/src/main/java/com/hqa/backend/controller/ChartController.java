package com.hqa.backend.controller;

import com.hqa.backend.dto.CandleHistoryResponse;
import com.hqa.backend.service.ChartService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/charts")
public class ChartController {

    private final ChartService chartService;

    public ChartController(ChartService chartService) {
        this.chartService = chartService;
    }

    @GetMapping("/{stockCode}/history")
    public CandleHistoryResponse history(@PathVariable String stockCode,
                                         @RequestParam(defaultValue = "1m") String timeframe,
                                         @RequestParam(defaultValue = "200") int count,
                                         @RequestParam(required = false) Long before) {
        return chartService.getHistoricalCandles(stockCode, timeframe, count, before);
    }
}
