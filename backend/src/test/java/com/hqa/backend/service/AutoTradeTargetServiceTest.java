package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserPreference;
import com.hqa.backend.entity.WatchlistItem;
import com.hqa.backend.entity.enums.InvestmentExperience;
import com.hqa.backend.entity.enums.InvestmentGoal;
import com.hqa.backend.entity.enums.InvestmentType;
import com.hqa.backend.entity.enums.LossAction;
import com.hqa.backend.entity.enums.LossTolerance;
import com.hqa.backend.entity.enums.OccupationType;
import com.hqa.backend.entity.enums.VolatilityTolerance;
import com.hqa.backend.repository.UserRepository;
import com.hqa.backend.repository.WatchlistItemRepository;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class AutoTradeTargetServiceTest {

    private final UserRepository userRepository = mock(UserRepository.class);
    private final WatchlistItemRepository watchlistRepository = mock(WatchlistItemRepository.class);
    private final AutoTradeTargetService service = new AutoTradeTargetService(userRepository, watchlistRepository);

    @Test
    void activeTargetsReturnAutoTradeUsersWithProfileAndWatchlistSymbols() {
        User user = user("user-1", true);
        UserPreference preference = preference(InvestmentType.MID_AGGRESSIVE);
        user.setPreference(preference);
        WatchlistItem samsung = item(user, "삼성전자", "005930", "KOSPI");
        WatchlistItem hynix = item(user, "SK하이닉스", "000660", "KOSPI");
        when(userRepository.findAllActiveWithSecretAndPreference()).thenReturn(List.of(user));
        when(watchlistRepository.findByUserOrderByCreatedAtDesc(user)).thenReturn(List.of(samsung, hynix));

        Map<String, Object> response = service.activeTargets();

        List<?> targets = (List<?>) response.get("targets");
        assertThat(targets).hasSize(1);
        Map<String, Object> target = castMap(targets.get(0));
        assertThat(target).containsEntry("userId", "user-1");
        assertThat(target).containsEntry("strategyProfile", "short");
        assertThat(castMap(target.get("investorProfile"))).containsEntry("investment_type", "MID_AGGRESSIVE");
        assertThat((List<?>) target.get("symbols")).hasSize(2);
    }

    @Test
    void activeTargetsIncludeAutoTradeUsersWithoutSymbolsForThemeWideAnalysis() {
        User off = user("off", false);
        off.setPreference(preference(InvestmentType.STABLE));
        User empty = user("empty", true);
        empty.setPreference(preference(InvestmentType.STABLE));
        when(userRepository.findAllActiveWithSecretAndPreference()).thenReturn(List.of(off, empty));
        when(watchlistRepository.findByUserOrderByCreatedAtDesc(empty)).thenReturn(List.of());

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

    private static WatchlistItem item(User user, String name, String code, String market) {
        WatchlistItem item = new WatchlistItem();
        item.setUser(user);
        item.setStockName(name);
        item.setStockCode(code);
        item.setMarket(market);
        return item;
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
