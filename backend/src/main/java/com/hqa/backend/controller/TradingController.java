package com.hqa.backend.controller;

import com.hqa.backend.dto.AutoTradeStatusResponse;
import com.hqa.backend.dto.AutoTradeToggleRequest;
import com.hqa.backend.dto.DirectBuyRequest;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.TradeDecisionRequest;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.AutoTradeService;
import com.hqa.backend.service.HistoricalTradingSnapshotService;
import com.hqa.backend.service.KisClient;
import com.hqa.backend.service.TradeSignalService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.Valid;
import java.util.HashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "매매", description = "자동매매 토글·매매 판단·직접 주문(매수/매도)·잔고·주문내역 (로그인 필요)")
@RestController
@RequestMapping("/api/v1/trading")
public class TradingController {

    private final AiServerClient aiServerClient;
    private final AutoTradeService autoTradeService;
    private final AuthService authService;
    private final KisClient kisClient;
    private final TradeSignalService tradeSignalService;
    private final HistoricalTradingSnapshotService historicalTradingSnapshotService;

    public TradingController(AiServerClient aiServerClient, AutoTradeService autoTradeService,
                             AuthService authService, KisClient kisClient,
                             TradeSignalService tradeSignalService,
                             HistoricalTradingSnapshotService historicalTradingSnapshotService) {
        this.aiServerClient = aiServerClient;
        this.autoTradeService = autoTradeService;
        this.authService = authService;
        this.kisClient = kisClient;
        this.tradeSignalService = tradeSignalService;
        this.historicalTradingSnapshotService = historicalTradingSnapshotService;
    }

    @Operation(summary = "자동매매 상태 조회", description = "사용자의 자동매매 활성 여부와 AI 서버 매매 상태를 조회한다.")
    @GetMapping("/status")
    public AutoTradeStatusResponse status(HttpSession session) {
        User user = authService.requireUser(session);
        return new AutoTradeStatusResponse(autoTradeService.isEnabled(user),
                Map.of("available", aiServerClient.isAvailable(), "accountMode", "PAPER"));
    }

    @Operation(summary = "자동매매 토글", description = "사용자의 자동매매를 켜거나 끈다.")
    @PostMapping("/auto")
    public AutoTradeStatusResponse toggleAuto(@Valid @RequestBody AutoTradeToggleRequest request,
                                              HttpSession session) {
        User user = authService.requireUser(session);
        boolean requestedEnabled = Boolean.TRUE.equals(request.getEnabled());
        boolean enabled = autoTradeService.setEnabled(user, requestedEnabled);
        return new AutoTradeStatusResponse(enabled, Map.of("autoTradeEnabled", enabled, "accountMode", "PAPER"));
    }

    @Operation(summary = "이전 매매 판단 미리보기 (종료)", deprecated = true, description = "현재 엔진에서 지원하지 않는 이전 경로. 410을 반환한다.")
    @PostMapping("/decision/preview")
    public Map<String, Object> preview(@Valid @RequestBody TradeDecisionRequest request,
                                       HttpSession session) {
        authService.requireUser(session);
        throw retiredDecisionEndpoint();
    }

    @Operation(summary = "이전 매매 판단 실행 (종료)", deprecated = true, description = "현재 엔진에서 지원하지 않는 이전 경로. 410을 반환한다.")
    @PostMapping("/decision/execute")
    public Map<String, Object> execute(@Valid @RequestBody TradeDecisionRequest request,
                                       HttpSession session) {
        authService.requireUser(session);
        throw retiredDecisionEndpoint();
    }

    @Operation(summary = "주문 내역", description = "주문 체결/접수 내역을 조회한다. date(yyyymmdd) 선택, limit 1~500(기본 50).")
    @GetMapping("/orders")
    public Map<String, Object> orders(@RequestParam(required = false) String date,
                                      @RequestParam(defaultValue = "50") int limit,
                                      HttpSession session) {
        User user = authService.requireUser(session);
        int boundedLimit = Math.max(1, Math.min(500, limit));
        return historicalTradingSnapshotService.orders(user.getUserId(), date, boundedLimit);
    }

    @Operation(summary = "매매 시그널 조회", description = "사용자에게 생성된 최근 매매 시그널 목록을 조회한다.")
    @GetMapping("/signals")
    public Map<String, Object> signals(HttpSession session) {
        User user = authService.requireUser(session);
        return Map.of("items", tradeSignalService.recentForUser(user.getUserId()));
    }

    @Operation(summary = "AI 자동매매 근거 조회", description = "최근 자동매매 신호의 최종 판단, 에이전트별 근거, 주문 결과를 조회한다.")
    @GetMapping("/explanations")
    public Map<String, Object> explanations(@RequestParam(defaultValue = "10") int limit,
                                            HttpSession session) {
        User user = authService.requireUser(session);
        int boundedLimit = Math.max(1, Math.min(50, limit));
        return Map.of("items", tradeSignalService.recentExplanationsForUser(user.getUserId(), boundedLimit));
    }

    @Operation(summary = "계좌 잔고 조회",
            description = "KIS 계좌 잔고를 조회한다. KIS 미설정 시 400, 토큰 발급 실패 시 503.")
    @GetMapping("/balance")
    public Map<String, Object> balance(HttpSession session) {
        User user = authService.requireUser(session);
        UserSecret secret = user.getSecret();
        if (secret == null || isBlank(secret.getKisAppKey()) || isBlank(secret.getKisAppSecret())
                || isBlank(secret.getKisAccountNo())) {
            throw new ApiException(ErrorCode.KIS_SECRET_NOT_CONFIGURED, 400, "KIS 계좌가 설정되지 않았습니다", null);
        }
        String token = kisClient.fetchAccessToken(user.getUserId(), secret);
        if (token == null || token.isBlank()) {
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503, "KIS 토큰 발급 실패", null);
        }
        Map<String, Object> result;
        try {
            result = kisClient.inquireBalance(user.getUserId(), secret, token);
        } catch (RuntimeException ex) {
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503, "KIS 잔고를 조회하지 못했습니다", null);
        }
        if (!Boolean.TRUE.equals(result.get("success"))) {
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503, "KIS 잔고를 조회하지 못했습니다", null);
        }
        return result;
    }

    @Operation(summary = "AI 운용 요약", description = "최근 multi-theme 주도주 선별과 에이전트 판단 요약을 조회한다.")
    @GetMapping("/ai-activity")
    public Map<String, Object> aiActivity(@RequestParam(defaultValue = "6") int limit,
                                          HttpSession session) {
        User user = authService.requireUser(session);
        return historicalTradingSnapshotService.aiActivity(user.getUserId(), Math.max(1, Math.min(20, limit)));
    }

    @Operation(summary = "직접 매수 주문", description = "KIS로 직접 매수 주문을 낸다. limit_price=0이면 시장가 주문.")
    @PostMapping("/buy")
    public Map<String, Object> directBuy(@Valid @RequestBody DirectBuyRequest request, HttpSession session) {
        return executeDirectOrder(request, session, /* isBuy = */ true);
    }

    @Operation(summary = "직접 매도 주문", description = "KIS로 직접 매도 주문을 낸다. limit_price=0이면 시장가 주문.")
    @PostMapping("/sell")
    public Map<String, Object> directSell(@Valid @RequestBody DirectBuyRequest request, HttpSession session) {
        return executeDirectOrder(request, session, /* isBuy = */ false);
    }

    private Map<String, Object> executeDirectOrder(DirectBuyRequest request, HttpSession session, boolean isBuy) {
        User user = authService.requireUser(session);
        UserSecret secret = user.getSecret();
        if (secret == null || isBlank(secret.getKisAppKey()) || isBlank(secret.getKisAppSecret())
                || isBlank(secret.getKisAccountNo())) {
            throw new ApiException(ErrorCode.KIS_SECRET_NOT_CONFIGURED, 400,
                    "KIS API 키가 설정되어 있지 않습니다", null);
        }
        String token = kisClient.fetchAccessToken(user.getUserId(), secret);
        if (token == null) {
            throw new ApiException(ErrorCode.SERVICE_UNAVAILABLE, 503,
                    "KIS 토큰 발급 실패", null);
        }
        Map<String, Object> result = isBuy
                ? kisClient.buy(user.getUserId(), secret, token,
                        request.getStockCode(), request.getQuantity(), request.getLimitPrice())
                : kisClient.sell(user.getUserId(), secret, token,
                        request.getStockCode(), request.getQuantity(), request.getLimitPrice());
        Map<String, Object> response = new HashMap<>();
        response.put("stockName", request.getStockName());
        response.put("stockCode", request.getStockCode());
        response.put("quantity", request.getQuantity());
        response.put("limitPrice", request.getLimitPrice());
        response.put("side", isBuy ? "buy" : "sell");
        response.putAll(result);
        return response;
    }

    private ApiException retiredDecisionEndpoint() {
        return new ApiException(ErrorCode.INVALID_REQUEST, 410,
                "이전 매매 판단 API는 종료되었습니다. 종목 분석은 /api/v1/analysis, 모의 자동매매 설정은 /api/v1/trading/auto를 사용하세요", null);
    }

    private boolean isBlank(String s) {
        return s == null || s.isBlank();
    }
}
