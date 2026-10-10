package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record NicknameRequest(
        @NotBlank @Size(min = 2, max = 30)
        @Pattern(regexp = "[\\p{L}\\p{N}_. -]+", message = "Use letters, numbers, spaces, '.', '_' or '-'")
        String nickname) { }
