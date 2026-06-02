package com.hqa.backend.controller;

import com.hqa.backend.dto.CandleHistoryResponse;
import com.hqa.backend.service.ChartService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpSession;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "차트", description = "과거 캔들 조회 (실시간 차트는 WebSocket /api/v1/charts/ws/{stockCode})")
@RestController
@RequestMapping("/api/v1/charts")
public class ChartController {

    private final ChartService chartService;

    public ChartController(ChartService chartService) {
        this.chartService = chartService;
    }

    @Operation(summary = "과거 캔들 조회",
            description = "종목의 과거 캔들 데이터를 조회한다. timeframe(예 1m), count, before(페이징 기준 시각)로 범위를 지정한다.")
    @GetMapping("/{stockCode}/history")
    public CandleHistoryResponse history(@PathVariable String stockCode,
                                         @RequestParam(defaultValue = "1m") String timeframe,
                                         @RequestParam(defaultValue = "200") int count,
                                         @RequestParam(required = false) Long before,
                                         HttpSession session) {
        return chartService.getHistoricalCandles(stockCode, timeframe, count, before, session);
    }
}
