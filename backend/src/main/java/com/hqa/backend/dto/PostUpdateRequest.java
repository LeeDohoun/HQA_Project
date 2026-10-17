package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record PostUpdateRequest(
        @NotBlank @Size(max = 160) String title,
        @NotBlank @Size(max = 20000) String content
) {
}
