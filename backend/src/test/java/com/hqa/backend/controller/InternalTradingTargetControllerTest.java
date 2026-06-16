package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.service.AutoTradeTargetService;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class InternalTradingTargetControllerTest {

    @Test
    void activeTargetsRequireInternalTokenAndReturnServicePayload() {
        HqaProperties properties = new HqaProperties();
        properties.setInternalToken("secret");
        AutoTradeTargetService service = mock(AutoTradeTargetService.class);
        when(service.activeTargets()).thenReturn(Map.of(
                "targets", List.of(Map.of("userId", "user-1"))
        ));
        InternalTradingTargetController controller = new InternalTradingTargetController(service, properties);

        Map<String, Object> response = controller.activeTargets("secret");

        assertThat((List<?>) response.get("targets")).hasSize(1);
    }

    @Test
    void activeTargetsRejectInvalidInternalToken() {
        HqaProperties properties = new HqaProperties();
        properties.setInternalToken("secret");
        InternalTradingTargetController controller = new InternalTradingTargetController(
                mock(AutoTradeTargetService.class),
                properties
        );

        assertThatThrownBy(() -> controller.activeTargets("wrong"))
                .isInstanceOf(ApiException.class);
    }
}
