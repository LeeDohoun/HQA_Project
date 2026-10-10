package com.hqa.backend.dto;

import java.util.List;

public record PostListResponse(
        List<PostSummary> items,
        int page,
        int size,
        long totalItems,
        int totalPages
) {
}
