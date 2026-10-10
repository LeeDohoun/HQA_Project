package com.hqa.backend.dto;

import java.time.OffsetDateTime;

public record CommentResponse(
        String id,
        String content,
        AuthorResponse author,
        boolean mine,
        boolean deletable,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {
}
