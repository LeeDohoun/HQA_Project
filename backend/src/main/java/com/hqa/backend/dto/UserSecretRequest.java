package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;

public record UserSecretRequest(
        @NotBlank String kisAppKey,
        @NotBlank String kisAppSecret,
        @NotBlank String kisAccountNo
) {
}
