package com.hqa.backend.dto;

import jakarta.validation.constraints.NotNull;

public class AutoTradeToggleRequest {

    @NotNull
    private Boolean enabled;

    public Boolean getEnabled() { return enabled; }
    public void setEnabled(Boolean enabled) { this.enabled = enabled; }
}
