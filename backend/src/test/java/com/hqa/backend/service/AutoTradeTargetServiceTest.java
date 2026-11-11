package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserPreference;
import com.hqa.backend.entity.enums.InvestmentExperience;
import com.hqa.backend.entity.enums.InvestmentGoal;
import com.hqa.backend.entity.enums.InvestmentType;
import com.hqa.backend.entity.enums.LossAction;
import com.hqa.backend.entity.enums.LossTolerance;
import com.hqa.backend.entity.enums.OccupationType;
import com.hqa.backend.entity.enums.VolatilityTolerance;
import com.hqa.backend.repository.UserRepository;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class AutoTradeTargetServiceTest {

    private final UserRepository userRepository = mock(UserRepository.class);
    private final AutoTradeTargetService service = new AutoTradeTargetService(userRepository);

    @Test
    void activeTargetsReturnAutoTradeUsersWithProfileAndNoSymbolLimit() {
        User user = user("user-1", true);
        UserPreference preference = preference(InvestmentType.MID_AGGRESSIVE);
        user.setPreference(preference);
        when(userRepository.findAllActiveWithSecretAndPreference()).thenReturn(List.of(user));

        Map<String, Object> response = service.activeTargets();

        List<?> targets = (List<?>) response.get("targets");
        assertThat(targets).hasSize(1);
        Map<String, Object> target = castMap(targets.get(0));
        assertThat(target).containsEntry("userId", "user-1");
        assertThat(target).containsEntry("strategyProfile", "short");
        assertThat(castMap(target.get("investorProfile"))).containsEntry("investment_type", "MID_AGGRESSIVE");
        // A watchlist is for following stocks; it must not narrow what auto-trading may consider.
        assertThat((List<?>) target.get("symbols")).isEmpty();
    }

    @Test
    void activeTargetsIncludeAutoTradeUsersWithoutSymbolsForThemeWideAnalysis() {
        User off = user("off", false);
        off.setPreference(preference(InvestmentType.STABLE));
        User empty = user("empty", true);
        empty.setPreference(preference(InvestmentType.STABLE));
        when(userRepository.findAllActiveWithSecretAndPreference()).thenReturn(List.of(off, empty));

        Map<String, Object> response = service.activeTargets();

        List<?> targets = (List<?>) response.get("targets");
        assertThat(targets).hasSize(1);
        Map<String, Object> target = castMap(targets.get(0));
        assertThat(target).containsEntry("userId", "empty");
        assertThat((List<?>) target.get("symbols")).isEmpty();
        assertThat((List<?>) target.get("themeKeys")).isEmpty();
    }

    private static User user(String userId, boolean enabled) {
        User user = new User();
        user.setUserId(userId);
        user.setAutoTradeEnabled(enabled);
        return user;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castMap(Object value) {
        return (Map<String, Object>) value;
    }

    private static UserPreference preference(InvestmentType investmentType) {
        UserPreference preference = new UserPreference();
        preference.setTotalAssets(10_000_000L);
        preference.setMonthlyInvestment(500_000L);
        preference.setInvestmentPeriodMonths(12);
        preference.setTargetReturnRate(10);
        preference.setInvestmentGoal(InvestmentGoal.ASSET_GROWTH);
        preference.setInvestmentExperience(InvestmentExperience.INTERMEDIATE);
        preference.setBirthDate(LocalDate.of(1990, 1, 1));
        preference.setInvestmentType(investmentType);
        preference.setVolatilityTolerance(VolatilityTolerance.MEDIUM);
        preference.setLossAction(LossAction.HOLD);
        preference.setLeverageAllowed(false);
        preference.setOccupationType(OccupationType.EMPLOYEE);
        preference.setLossTolerance(LossTolerance.LEVEL_2);
        return preference;
    }
}
