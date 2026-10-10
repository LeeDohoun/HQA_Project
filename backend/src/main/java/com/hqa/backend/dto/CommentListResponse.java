package com.hqa.backend.dto;

import java.util.List;

public record CommentListResponse(
        List<CommentResponse> items,
        String nextCursor,
        boolean hasMore,
        int totalItems
) {
}
