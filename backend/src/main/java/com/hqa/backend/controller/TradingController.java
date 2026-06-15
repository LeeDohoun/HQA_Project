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
import java.util.List;
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
        Map<String, Object> aiStatus;
        try {
            aiStatus = aiServerClient.getTradingStatus();
        } catch (Exception ignored) {
            aiStatus = Map.of();
        }
        return new AutoTradeStatusResponse(autoTradeService.isEnabled(user), aiStatus);
    }

    @Operation(summary = "자동매매 토글", description = "사용자의 자동매매를 켜거나 끈다.")
    @PostMapping("/auto")
    public AutoTradeStatusResponse toggleAuto(@Valid @RequestBody AutoTradeToggleRequest request,
                                              HttpSession session) {
        User user = authService.requireUser(session);
        boolean requestedEnabled = Boolean.TRUE.equals(request.getEnabled());
        Map<String, Object> aiStatus = requestedEnabled
                ? aiServerClient.startPaperTradingLoop(user.getUserId())
                : aiServerClient.stopPaperTradingLoop();
        boolean enabled = autoTradeService.setEnabled(user, requestedEnabled);
        return new AutoTradeStatusResponse(enabled, aiStatus);
    }

    @Operation(summary = "매매 판단 미리보기", description = "분석 결과 기반 매매 판단을 미리 계산한다. 실제 주문은 발생하지 않는다.")
    @PostMapping("/decision/preview")
    public Map<String, Object> preview(@Valid @RequestBody TradeDecisionRequest request,
                                       HttpSession session) {
        User user = authService.requireUser(session);
        return aiServerClient.previewTradeDecision(buildAiPayload(request, false, user));
    }

    @Operation(summary = "매매 판단 실행", description = "매매 판단을 실행한다. 자동매매 설정에 따라 실제 KIS 주문이 발생할 수 있다.")
    @PostMapping("/decision/execute")
    public Map<String, Object> execute(@Valid @RequestBody TradeDecisionRequest request,
                                       HttpSession session) {
        User user = authService.requireUser(session);
        return aiServerClient.executeTradeDecision(buildAiPayload(request, true, user));
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
            return historicalTradingSnapshotService.balance(user.getUserId());
        }
        String token = kisClient.fetchAccessToken(user.getUserId(), secret);
        if (token == null) {
            return historicalTradingSnapshotService.balance(user.getUserId());
        }
        try {
            return kisClient.inquireBalance(user.getUserId(), secret, token);
        } catch (RuntimeException ignored) {
            return historicalTradingSnapshotService.balance(user.getUserId());
        }
    }

    @Operation(summary = "AI 운용 요약", description = "최근 multi-theme 주도주 선별과 에이전트 판단 요약을 조회한다.")
    @GetMapping("/ai-activity")
    public Map<String, Object> aiActivity(@RequestParam(defaultValue = "6") int limit,
                                          HttpSession session) {
        authService.requireUser(session);
        return historicalTradingSnapshotService.aiActivity(Math.max(1, Math.min(20, limit)));
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

    private Map<String, Object> buildAiPayload(TradeDecisionRequest request, boolean execute, User user) {
        Map<String, Object> decision = new HashMap<>();
        var d = request.getFinalDecision();
        decision.put("total_score", d.getTotalScore());
        decision.put("action", d.getAction());
        decision.put("action_code", d.getActionCode());
        decision.put("confidence", d.getConfidence());
        decision.put("risk_level", d.getRiskLevel());
        decision.put("risk_level_code", d.getRiskLevelCode());
        decision.put("summary", d.getSummary());
        decision.put("key_catalysts", d.getKeyCatalysts() == null ? List.of() : d.getKeyCatalysts());
        decision.put("risk_factors", d.getRiskFactors() == null ? List.of() : d.getRiskFactors());
        decision.put("detailed_reasoning", d.getDetailedReasoning());
        decision.put("position_size", d.getPositionSize());
        decision.put("entry_strategy", d.getEntryStrategy());
        decision.put("exit_strategy", d.getExitStrategy());
        decision.put("stop_loss", d.getStopLoss());
        decision.put("signal_alignment", d.getSignalAlignment());
        decision.put("contrarian_view", d.getContrarianView());

        Map<String, Object> payload = new HashMap<>();
        payload.put("stock_name", request.getStockName());
        payload.put("stock_code", request.getStockCode());
        payload.put("final_decision", decision);
        payload.put("quantity", request.getQuantity());
        if (request.getCurrentPrice() != null) payload.put("current_price", request.getCurrentPrice());
        if (request.getDryRunOverride() != null) payload.put("dry_run_override", request.getDryRunOverride());
        Boolean tradingOverride = request.getTradingEnabledOverride();
        if (tradingOverride == null && execute) {
            tradingOverride = autoTradeService.isEnabled(user);
        }
        if (tradingOverride != null) payload.put("trading_enabled_override", tradingOverride);
        return payload;
    }

    private boolean isBlank(String s) {
        return s == null || s.isBlank();
    }
}
