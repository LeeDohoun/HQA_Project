package com.hqa.backend.controller;

import com.hqa.backend.dto.RealtimePriceResponse;
import com.hqa.backend.dto.StockSearchResponse;
import com.hqa.backend.service.StockService;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.constraints.NotBlank;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
@RequestMapping("/api/v1/stocks")
public class StockController {

    private final StockService stockService;

    public StockController(StockService stockService) {
        this.stockService = stockService;
    }

    @GetMapping("/search")
    public StockSearchResponse search(@RequestParam("q") @NotBlank String query) {
        return stockService.search(query);
    }

    @GetMapping("/{stockCode}/price")
    public RealtimePriceResponse price(@PathVariable String stockCode, HttpSession session) {
        return stockService.getRealtimePrice(stockCode, session);
    }
}
