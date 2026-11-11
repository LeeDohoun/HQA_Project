package com.hqa.backend.service;

import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserPreference;
import com.hqa.backend.repository.UserRepository;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AutoTradeTargetService {

    private final UserRepository userRepository;

    public AutoTradeTargetService(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> activeTargets() {
        List<Map<String, Object>> targets = userRepository.findAllActiveWithSecretAndPreference().stream()
                .filter(User::isAutoTradeEnabled)
                .map(this::targetFor)
                .toList();
        return Map.of("targets", targets);
    }

    private Map<String, Object> targetFor(User user) {
        Map<String, Object> target = new HashMap<>();
        target.put("userId", user.getUserId());
        target.put("strategyProfile", strategyProfile(user.getPreference()));
        target.put("investorProfile", investorProfile(user.getPreference()));
        // The watchlist is for following and analysing stocks, not a limit on auto-trading. Sending it
        // here restricted every cycle to the listed stocks, so adding one to run an analysis silently
        // narrowed trading to it. With no symbols the AI server reviews its top candidates and holdings.
        target.put("symbols", List.of());
        target.put("themeKeys", List.of());
        return target;
    }

    private Map<String, Object> investorProfile(UserPreference p) {
        if (p == null) {
            return Map.of();
        }
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
        if (p == null || p.getInvestmentType() == null) {
            return "default";
        }
        return switch (p.getInvestmentType()) {
            case STABLE, MID_STABLE -> "long";
            case MID_AGGRESSIVE, AGGRESSIVE -> "short";
            default -> "default";
        };
    }
}
