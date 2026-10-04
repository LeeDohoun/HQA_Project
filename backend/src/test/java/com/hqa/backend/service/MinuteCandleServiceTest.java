package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.hqa.backend.dto.CandleData;
import com.hqa.backend.dto.InternalMinuteCandleResponse;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.UserRepository;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class MinuteCandleServiceTest {

    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private final LocalDate date = LocalDate.now(KST).minusDays(1);
    private final UserRepository users = mock(UserRepository.class);
    private final KisClient kis = mock(KisClient.class);
    private final MinuteCandleService service = new MinuteCandleService(users, kis);

    @Test
    void pagesBackwardsDeduplicatesAndSortsFullSession() {
        UserSecret secret = paperUser();
        when(kis.fetchDailyMinuteCandles("u1", secret, "token", "005930", date, "153000"))
                .thenReturn(page("13:31", "15:30"));
        when(kis.fetchDailyMinuteCandles("u1", secret, "token", "005930", date, "133059"))
                .thenReturn(page("11:32", "13:31"));
        when(kis.fetchDailyMinuteCandles("u1", secret, "token", "005930", date, "113159"))
                .thenReturn(page("09:33", "11:32"));
        when(kis.fetchDailyMinuteCandles("u1", secret, "token", "005930", date, "093259"))
                .thenReturn(page("09:00", "09:33"));

        InternalMinuteCandleResponse result = service.getMinuteCandles("u1", "005930", date);

        assertThat(result.status()).isEqualTo("OK");
        assertThat(result.pages()).isEqualTo(4);
        assertThat(result.source()).isEqualTo("kis");
        assertThat(result.candles()).hasSize(391);
        assertThat(result.candles()).extracting(c -> c.time()).isSorted().doesNotHaveDuplicates();
        assertThat(result.candles().get(0).time()).isEqualTo(candle("09:00").time());
        assertThat(result.candles().get(390).time()).isEqualTo(candle("15:30").time());
        verify(kis, times(4)).fetchDailyMinuteCandles(eq("u1"), eq(secret), eq("token"), eq("005930"), eq(date), anyString());
    }

    @Test
    void stopsWhenPageHasNoOlderBar() {
        UserSecret secret = paperUser();
        when(kis.fetchDailyMinuteCandles(eq("u1"), eq(secret), eq("token"), eq("005930"), eq(date), anyString()))
                .thenReturn(List.of(candle("15:20"), candle("12:00")));
        var result = service.getMinuteCandles("u1", "005930", date);
        assertThat(result.status()).isEqualTo("OK");
        assertThat(result.pages()).isEqualTo(2);
        assertThat(result.candles()).hasSize(2);
    }

    @Test
    void excludesOtherDatesAndOutsideRegularSession() {
        UserSecret secret = paperUser();
        CandleData previousDay = new CandleData(candle("15:30").time() - 86400, 100, 110, 90, 105, 20, true);
        when(kis.fetchDailyMinuteCandles("u1", secret, "token", "005930", date, "153000"))
                .thenReturn(List.of(previousDay, candle("08:59"), candle("09:00"), candle("15:30"), candle("15:31")));
        var result = service.getMinuteCandles("u1", "005930", date);
        assertThat(result.status()).isEqualTo("OK");
        assertThat(result.candles()).extracting(c -> c.time()).containsExactly(candle("09:00").time(), candle("15:30").time());
    }

    @Test
    void capsCallsEvenIfEveryPageAdvances() {
        UserSecret secret = paperUser();
        when(kis.fetchDailyMinuteCandles(eq("u1"), eq(secret), eq("token"), eq("005930"), eq(date), anyString()))
                .thenReturn(List.of(candle("15:30")), List.of(candle("15:29")), List.of(candle("15:28")),
                        List.of(candle("15:27")), List.of(candle("15:26")), List.of(candle("15:25")));
        var result = service.getMinuteCandles("u1", "005930", date);
        assertThat(result.status()).isEqualTo("PAGE_LIMIT_REACHED");
        assertThat(result.pages()).isEqualTo(6);
        assertThat(result.candles()).isEmpty();
        verify(kis, times(6)).fetchDailyMinuteCandles(eq("u1"), eq(secret), eq("token"), eq("005930"), eq(date), anyString());
    }

    @Test
    void emptyFirstPageIsAnExplicitFailure() {
        paperUser();
        var result = service.getMinuteCandles("u1", "005930", date);
        assertThat(result.status()).isEqualTo("MINUTE_CANDLES_UNAVAILABLE");
        assertThat(result.pages()).isEqualTo(1);
        assertThat(result.candles()).isEmpty();
    }

    @Test
    void emptyLaterPageDoesNotSilentlySucceedWithPartialData() {
        UserSecret secret = paperUser();
        when(kis.fetchDailyMinuteCandles(eq("u1"), eq(secret), eq("token"), eq("005930"), eq(date), anyString()))
                .thenReturn(List.of(candle("15:30")), List.of());
        var result = service.getMinuteCandles("u1", "005930", date);
        assertThat(result.status()).isEqualTo("MINUTE_CANDLES_UNAVAILABLE");
        assertThat(result.pages()).isEqualTo(2);
        assertThat(result.candles()).isEmpty();
    }

    @Test
    void refusesRealAccountBeforeFetchingToken() {
        User user = new User();
        UserSecret secret = new UserSecret();
        secret.setKisIsReal(true);
        user.setSecret(secret);
        when(users.findByUserId("u1")).thenReturn(Optional.of(user));
        assertFailure("PAPER_ACCOUNT_REQUIRED", date, "005930");
        verifyNoInteractions(kis);
    }

    @Test
    void unknownUserFails() {
        assertFailure("USER_NOT_FOUND", date, "005930");
        verifyNoInteractions(kis);
    }

    @Test
    void missingSecretFails() {
        when(users.findByUserId("u1")).thenReturn(Optional.of(new User()));
        assertFailure("KIS_SECRET_MISSING", date, "005930");
        verifyNoInteractions(kis);
    }

    @ParameterizedTest
    @ValueSource(strings = {"key", "secret", "account", "product"})
    void missingSecretFieldFails(String field) {
        UserSecret secret = paperUser();
        switch (field) {
            case "key" -> secret.setKisAppKey(" ");
            case "secret" -> secret.setKisAppSecret(null);
            case "account" -> secret.setKisAccountNo("");
            case "product" -> secret.setKisAccountProductCode(null);
        }
        assertFailure("KIS_SECRET_MISSING", date, "005930");
        verifyNoInteractions(kis);
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = " ")
    void unavailableTokenFails(String token) {
        UserSecret secret = paperUser();
        when(kis.fetchAccessToken("u1", secret)).thenReturn(token);
        assertFailure("KIS_TOKEN_UNAVAILABLE", date, "005930");
    }

    @Test
    void futureDateFailsBeforeAccountLookup() {
        assertFailure("DATE_IN_FUTURE", LocalDate.now(KST).plusDays(1), "005930");
        verifyNoInteractions(users, kis);
    }

    @Test
    void tooOldDateFailsBeforeAccountLookup() {
        assertFailure("DATE_TOO_OLD", LocalDate.now(KST).minusDays(367), "005930");
        verifyNoInteractions(users, kis);
    }

    @Test
    void retentionBoundaryIsAccepted() {
        assertFailure("USER_NOT_FOUND", LocalDate.now(KST).minusDays(366), "005930");
        verify(users).findByUserId("u1");
    }

    @Test
    void nullDateFails() {
        assertFailure("INVALID_DATE", null, "005930");
        verifyNoInteractions(users, kis);
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"00593", "0059300", "ABCDEF", " 005930", "００５９３０"})
    void invalidStockCodeFails(String code) {
        assertFailure("INVALID_STOCK_CODE", date, code);
        verifyNoInteractions(users, kis);
    }

    private void assertFailure(String status, LocalDate day, String code) {
        var result = service.getMinuteCandles("u1", code, day);
        assertThat(result.status()).isEqualTo(status);
        assertThat(result.pages()).isZero();
        assertThat(result.candles()).isEmpty();
    }

    private UserSecret paperUser() {
        User user = new User();
        UserSecret secret = new UserSecret();
        secret.setKisAppKey("encrypted-key");
        secret.setKisAppSecret("encrypted-secret");
        secret.setKisAccountNo("encrypted-account");
        secret.setKisAccountProductCode("01");
        user.setSecret(secret);
        when(users.findByUserId("u1")).thenReturn(Optional.of(user));
        when(kis.fetchAccessToken("u1", secret)).thenReturn("token");
        return secret;
    }

    private CandleData candle(String time) {
        return new CandleData(date.atTime(LocalTime.parse(time)).atZone(KST).toEpochSecond(), 100, 110, 90, 105, 20, true);
    }

    private List<CandleData> page(String start, String end) {
        List<CandleData> rows = new ArrayList<>();
        for (LocalTime time = LocalTime.parse(end); !time.isBefore(LocalTime.parse(start)); time = time.minusMinutes(1)) {
            rows.add(candle(time.toString()));
        }
        return rows;
    }
}
