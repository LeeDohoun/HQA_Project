package com.hqa.backend.dto;

public record AuthResponse(
        boolean success,
        String message,
        AuthUserResponse user
) {
}
