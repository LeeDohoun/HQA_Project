package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.InternalPriceSnapshotRequest;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.PriceSnapshotService;
import java.util.List;
import org.junit.jupiter.api.Test;

class InternalMarketControllerTest {

    @Test
    void priceSnapshotsRejectsInvalidInternalToken() {
        HqaProperties properties = new HqaProperties();
        properties.setInternalToken("expected-token");
        InternalMarketController controller = new InternalMarketController(
                mock(PriceSnapshotService.class),
                properties
        );

        assertThatThrownBy(() -> controller.priceSnapshots(
                new InternalPriceSnapshotRequest("user-1", List.of("005930")),
                "bad-token"
        ))
                .isInstanceOf(ApiException.class)
                .hasMessage("Invalid internal token");
    }
}
