package com.hqa.backend.controller;

import com.hqa.backend.dto.RealtimePriceResponse;
import com.hqa.backend.dto.StockSearchResponse;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.KisClient;
import com.hqa.backend.service.StockService;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.constraints.NotBlank;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
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
    private final AiServerClient aiServerClient;
    private final AuthService authService;
    private final KisClient kisClient;

    public StockController(StockService stockService, AiServerClient aiServerClient,
                           AuthService authService, KisClient kisClient) {
        this.stockService = stockService;
        this.aiServerClient = aiServerClient;
        this.authService = authService;
        this.kisClient = kisClient;
    }

    @GetMapping("/search")
    public StockSearchResponse search(@RequestParam("q") @NotBlank String query) {
        return stockService.search(query);
    }

    @GetMapping("/{stockCode}/price")
    public RealtimePriceResponse price(@PathVariable String stockCode, HttpSession session) {
        return stockService.getRealtimePrice(stockCode, session);
    }

    @GetMapping("/{stockCode}/news")
    public Map<String, Object> news(@PathVariable String stockCode,
                                    @RequestParam(defaultValue = "20") int limit) {
        return aiServerClient.getStockNews(stockCode, clamp(limit));
    }

    @GetMapping("/{stockCode}/disclosures")
    public Map<String, Object> disclosures(@PathVariable String stockCode,
                                           @RequestParam(defaultValue = "20") int limit) {
        return aiServerClient.getStockDisclosures(stockCode, clamp(limit));
    }

/**
     * 시장 지수 (코스피·코스닥) 일괄 조회.
     * KIS 키가 없으면 빈 리스트. 로그인된 사용자의 KIS 토큰으로 호출.
     */
    @GetMapping("/indices")
    public Map<String, Object> indices(HttpSession session) {
        User user = authService.requireUser(session);
        UserSecret secret = user.getSecret();
        Map<String, Object> response = new LinkedHashMap<>();
        if (secret == null || isBlank(secret.getKisAppKey()) || isBlank(secret.getKisAppSecret())) {
            response.put("items", List.of());
            response.put("configured", false);
            return response;
        }
        String token = kisClient.fetchAccessToken(user.getUserId(), secret);
        if (token == null) {
            response.put("items", List.of());
            response.put("configured", true);
            response.put("error", "KIS 토큰 발급 실패");
            return response;
        }
        List<Map<String, Object>> items = new ArrayList<>();
        String[][] targets = { { "0001", "코스피" }, { "1001", "코스닥" } };
        for (String[] t : targets) {
            Map<String, Object> idx = kisClient.inquireIndexPrice(user.getUserId(), secret, token, t[0]);
            if (idx != null) {
                idx.put("name", t[1]);
                items.add(idx);
            }
        }
        response.put("items", items);
        response.put("configured", true);
        return response;
    }

    private static int clamp(int limit) {
        return Math.max(1, Math.min(100, limit));
    }

    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }
}
