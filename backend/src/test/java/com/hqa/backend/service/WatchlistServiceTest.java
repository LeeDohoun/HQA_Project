package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.hqa.backend.dto.WatchlistItemRequest;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.WatchlistItem;
import com.hqa.backend.repository.WatchlistItemRepository;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

class WatchlistServiceTest {

    private final WatchlistItemRepository repository = mock(WatchlistItemRepository.class);
    private final WatchlistService service = new WatchlistService(repository);

    @Test
    void listReturnsOnlyCurrentUsersWatchlistItems() {
        User user = user("user-1");
        WatchlistItem samsung = item(user, "삼성전자", "005930", "KOSPI");
        WatchlistItem hynix = item(user, "SK하이닉스", "000660", "KOSPI");
        when(repository.findByUserOrderByCreatedAtDesc(user)).thenReturn(List.of(hynix, samsung));

        var response = service.list(user);

        assertThat(response.items()).extracting("stockCode").containsExactly("000660", "005930");
    }

    @Test
    void addCreatesUserOwnedWatchlistItemWhenMissing() {
        User user = user("user-1");
        when(repository.findByUserAndStockCode(user, "005930")).thenReturn(Optional.empty());
        when(repository.save(org.mockito.Mockito.any(WatchlistItem.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.add(user, new WatchlistItemRequest("삼성전자", "005930", "KOSPI"));

        ArgumentCaptor<WatchlistItem> captor = ArgumentCaptor.forClass(WatchlistItem.class);
        verify(repository).save(captor.capture());
        assertThat(captor.getValue().getUser()).isEqualTo(user);
        assertThat(captor.getValue().getStockCode()).isEqualTo("005930");
        assertThat(captor.getValue().getStockName()).isEqualTo("삼성전자");
    }

    @Test
    void addUpdatesExistingItemInsteadOfDuplicatingStockCode() {
        User user = user("user-1");
        WatchlistItem existing = item(user, "삼성전자", "005930", "KOSPI");
        when(repository.findByUserAndStockCode(user, "005930")).thenReturn(Optional.of(existing));
        when(repository.save(existing)).thenReturn(existing);

        service.add(user, new WatchlistItemRequest("삼성전자우", "005930", "KOSPI"));

        assertThat(existing.getStockName()).isEqualTo("삼성전자우");
        verify(repository).save(existing);
    }

    @Test
    void deleteRemovesOnlyCurrentUsersStockCode() {
        User user = user("user-1");

        service.delete(user, "005930");

        verify(repository).deleteByUserAndStockCode(user, "005930");
    }

    private static User user(String userId) {
        User user = new User();
        user.setUserId(userId);
        return user;
    }

    private static WatchlistItem item(User user, String name, String code, String market) {
        WatchlistItem item = new WatchlistItem();
        item.setUser(user);
        item.setStockName(name);
        item.setStockCode(code);
        item.setMarket(market);
        return item;
    }
}
