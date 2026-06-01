package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.hqa.backend.dto.PriceSnapshotResponse;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.UserRepository;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class PriceSnapshotServiceTest {

    private final UserRepository userRepository = mock(UserRepository.class);
    private final KisClient kisClient = mock(KisClient.class);
    private final PriceSnapshotService service = new PriceSnapshotService(userRepository, kisClient);

    @Test
    void getSnapshotsReturnsKisCurrentPriceAndCachesShortRepeatedRequests() {
        User user = userWithSecret("user-1");
        when(userRepository.findByUserId("user-1")).thenReturn(Optional.of(user));
        when(kisClient.fetchAccessToken("user-1", user.getSecret())).thenReturn("token");
        when(kisClient.inquireCurrentPrice("user-1", user.getSecret(), "token", "005930")).thenReturn(72100L);

        List<PriceSnapshotResponse> first = service.getSnapshots("user-1", List.of("005930"));
        List<PriceSnapshotResponse> second = service.getSnapshots("user-1", List.of("005930"));

        assertThat(first).hasSize(1);
        assertThat(first.get(0).success()).isTrue();
        assertThat(first.get(0).currentPrice()).isEqualTo(72100L);
        assertThat(first.get(0).source()).isEqualTo("kis");
        assertThat(second.get(0).currentPrice()).isEqualTo(72100L);
        verify(kisClient, times(1)).inquireCurrentPrice("user-1", user.getSecret(), "token", "005930");
    }

    @Test
    void getSnapshotsReturnsFailurePerStockWhenKisSecretIsMissing() {
        User user = new User();
        user.setUserId("user-1");
        when(userRepository.findByUserId("user-1")).thenReturn(Optional.of(user));

        List<PriceSnapshotResponse> snapshots = service.getSnapshots("user-1", List.of("005930"));

        assertThat(snapshots).hasSize(1);
        assertThat(snapshots.get(0).success()).isFalse();
        assertThat(snapshots.get(0).failureReason()).isEqualTo("KIS_SECRET_MISSING");
    }

    private static User userWithSecret(String userId) {
        User user = new User();
        user.setUserId(userId);
        UserSecret secret = new UserSecret();
        secret.setKisAppKey("encrypted-key");
        secret.setKisAppSecret("encrypted-secret");
        secret.setKisAccountNo("encrypted-account");
        secret.setKisAccountProductCode("01");
        user.setSecret(secret);
        return user;
    }
}
