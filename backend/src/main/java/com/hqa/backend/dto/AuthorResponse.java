package com.hqa.backend.dto;

public record AuthorResponse(
        String id,
        String userId,
        String firstName,
        String lastName,
        String nickname,
        int level,
        String title
) {
}
