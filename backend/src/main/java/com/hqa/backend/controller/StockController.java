package com.hqa.backend.controller;

import com.hqa.backend.dto.RealtimePriceResponse;
import com.hqa.backend.dto.StockSearchResponse;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.KisClient;
import com.hqa.backend.service.StockService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
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

@Tag(name = "종목 / 시세", description = "종목 검색·실시간 시세·뉴스·공시·시장 지수 조회 (로그인 필요)")
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

    @Operation(summary = "종목 검색", description = "검색어(q)로 종목명/코드를 검색한다.")
    @GetMapping("/search")
    public StockSearchResponse search(@RequestParam("q") @NotBlank String query) {
        return stockService.search(query);
    }

    @Operation(summary = "실시간 시세", description = "종목의 현재가·등락·거래량 등 실시간 시세를 조회한다.")
    @GetMapping("/{stockCode}/price")
    public RealtimePriceResponse price(@PathVariable String stockCode, HttpSession session) {
        return stockService.getRealtimePrice(stockCode, session);
    }

    @Operation(summary = "종목 뉴스", description = "종목 관련 최신 뉴스를 조회한다. limit 1~100(기본 20).")
    @GetMapping("/{stockCode}/news")
    public Map<String, Object> news(@PathVariable String stockCode,
                                    @RequestParam(defaultValue = "20") int limit) {
        return aiServerClient.getStockNews(stockCode, clamp(limit));
    }

    @Operation(summary = "종목 공시", description = "종목 관련 최신 공시를 조회한다. limit 1~100(기본 20).")
    @GetMapping("/{stockCode}/disclosures")
    public Map<String, Object> disclosures(@PathVariable String stockCode,
                                           @RequestParam(defaultValue = "20") int limit) {
        return aiServerClient.getStockDisclosures(stockCode, clamp(limit));
    }

/**
     * 시장 지수 (코스피·코스닥) 일괄 조회.
     * KIS 키가 없으면 빈 리스트. 로그인된 사용자의 KIS 토큰으로 호출.
     */
    @Operation(summary = "시장 지수 조회",
            description = "코스피·코스닥 지수를 조회한다. KIS 키 미설정 시 빈 리스트와 configured=false를 반환한다.")
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
