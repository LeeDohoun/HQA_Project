package com.hqa.backend.scheduler;

import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserPreference;
import com.hqa.backend.repository.UserRepository;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.ErrorLogger;
import com.hqa.backend.service.TradeSignalService;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 15분마다 AI 서버가 저장한 대기 매매신호를 백엔드 책임으로 집행한다.
 */
@Component
public class TradingScheduler {

    private static final Logger log = LoggerFactory.getLogger(TradingScheduler.class);

    private final TradeSignalService tradeSignalService;
    private final UserRepository userRepository;
    private final AiServerClient aiServerClient;
    private final ErrorLogger errorLogger;

    public TradingScheduler(TradeSignalService tradeSignalService,
                            UserRepository userRepository,
                            AiServerClient aiServerClient,
                            ErrorLogger errorLogger) {
        this.tradeSignalService = tradeSignalService;
        this.userRepository = userRepository;
        this.aiServerClient = aiServerClient;
        this.errorLogger = errorLogger;
    }

    @Scheduled(fixedRate = 900_000)
    public void run() {
        try {
            requestSignalGeneration();
            tradeSignalService.processPendingSignals();
        } catch (Exception e) {
            log.error("[TradingScheduler] pending signal processing failed: {}", e.getMessage());
        }
    }

    private void requestSignalGeneration() {
        List<User> users = userRepository.findAllActiveWithSecretAndPreference()
                .stream()
                .filter(User::isAutoTradeEnabled)
                .toList();
        for (User user : users) {
            UserPreference preference = user.getPreference();
            if (preference == null) {
                continue;
            }
            Map<String, Object> payload = new HashMap<>();
            payload.put("user_id", user.getUserId());
            payload.put("investor_profile", investorProfile(preference));
            payload.put("strategy_profile", strategyProfile(preference));
            payload.put("execute", false);
            payload.put("preview", true);
            payload.put("dry_run", true);
            try {
                aiServerClient.submitMultiThemeTrade(payload);
            } catch (Exception e) {
                errorLogger.log("TradingScheduler", user.getUserId(), null,
                        "AI multi-theme signal generation failed", e.getMessage());
            }
        }
    }

    private Map<String, Object> investorProfile(UserPreference p) {
        Map<String, Object> profile = new HashMap<>();
        profile.put("total_assets", p.getTotalAssets());
        profile.put("monthly_investment", p.getMonthlyInvestment());
        profile.put("investment_period_months", p.getInvestmentPeriodMonths());
        profile.put("target_return_rate", p.getTargetReturnRate());
        profile.put("investment_goal", p.getInvestmentGoal() == null ? null : p.getInvestmentGoal().name());
        profile.put("investment_experience", p.getInvestmentExperience() == null ? null : p.getInvestmentExperience().name());
        profile.put("investment_type", p.getInvestmentType() == null ? null : p.getInvestmentType().name());
        profile.put("volatility_tolerance", p.getVolatilityTolerance() == null ? null : p.getVolatilityTolerance().name());
        profile.put("loss_action", p.getLossAction() == null ? null : p.getLossAction().name());
        profile.put("leverage_allowed", p.getLeverageAllowed());
        profile.put("occupation_type", p.getOccupationType() == null ? null : p.getOccupationType().name());
        profile.put("loss_tolerance", p.getLossTolerance() == null ? null : p.getLossTolerance().name());
        return profile;
    }

    private String strategyProfile(UserPreference p) {
        if (p.getInvestmentType() == null) {
            return "default";
        }
        return switch (p.getInvestmentType()) {
            case STABLE, MID_STABLE -> "long";
            case MID_AGGRESSIVE, AGGRESSIVE -> "short";
            default -> "default";
        };
    }
}
