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
import com.hqa.backend.service.KisClient;
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

@RestController
@RequestMapping("/api/v1/trading")
public class TradingController {

    private final AiServerClient aiServerClient;
    private final AutoTradeService autoTradeService;
    private final AuthService authService;
    private final KisClient kisClient;

    public TradingController(AiServerClient aiServerClient, AutoTradeService autoTradeService,
                             AuthService authService, KisClient kisClient) {
        this.aiServerClient = aiServerClient;
        this.autoTradeService = autoTradeService;
        this.authService = authService;
        this.kisClient = kisClient;
    }

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

    @PostMapping("/auto")
    public AutoTradeStatusResponse toggleAuto(@Valid @RequestBody AutoTradeToggleRequest request,
                                              HttpSession session) {
        User user = authService.requireUser(session);
        boolean enabled = autoTradeService.setEnabled(user, Boolean.TRUE.equals(request.getEnabled()));
        Map<String, Object> aiStatus;
        try {
            aiStatus = aiServerClient.getTradingStatus();
        } catch (Exception ignored) {
            aiStatus = Map.of();
        }
        return new AutoTradeStatusResponse(enabled, aiStatus);
    }

    @PostMapping("/decision/preview")
    public Map<String, Object> preview(@Valid @RequestBody TradeDecisionRequest request,
                                       HttpSession session) {
        User user = authService.requireUser(session);
        return aiServerClient.previewTradeDecision(buildAiPayload(request, false, user));
    }

    @PostMapping("/decision/execute")
    public Map<String, Object> execute(@Valid @RequestBody TradeDecisionRequest request,
                                       HttpSession session) {
        User user = authService.requireUser(session);
        return aiServerClient.executeTradeDecision(buildAiPayload(request, true, user));
    }

    @GetMapping("/orders")
    public Map<String, Object> orders(@RequestParam(required = false) String date,
                                      @RequestParam(defaultValue = "50") int limit) {
        return aiServerClient.getTradingOrders(date, Math.max(1, Math.min(500, limit)));
    }

    @PostMapping("/buy")
    public Map<String, Object> directBuy(@Valid @RequestBody DirectBuyRequest request, HttpSession session) {
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
        Map<String, Object> result = kisClient.buy(user.getUserId(), secret, token,
                request.getStockCode(), request.getQuantity(), request.getLimitPrice());
        Map<String, Object> response = new HashMap<>();
        response.put("stockName", request.getStockName());
        response.put("stockCode", request.getStockCode());
        response.put("quantity", request.getQuantity());
        response.put("limitPrice", request.getLimitPrice());
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
        decision.put("validation_status", d.getValidationStatus());
        decision.put("validation_summary", d.getValidationSummary());
        decision.put("validator_model", d.getValidatorModel());
        decision.put("primary_model", d.getPrimaryModel());
        decision.put("validator_action", d.getValidatorAction());
        decision.put("validator_confidence", d.getValidatorConfidence());

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
