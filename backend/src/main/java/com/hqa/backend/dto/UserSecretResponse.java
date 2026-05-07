package com.hqa.backend.dto;

public record UserSecretResponse(
        boolean configured,
        String kisAppKeyMasked,
        String kisAccountNoMasked
) {
}
