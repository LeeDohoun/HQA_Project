package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record AuthSignupRequest(
        @NotBlank @Size(min = 4, max = 50) String userId,
        @NotBlank @Size(max = 50) String firstName,
        @NotBlank @Size(max = 50) String lastName,
        @NotBlank @Size(min = 8, max = 100) String password
) {
}
