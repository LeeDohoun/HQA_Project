package com.hqa.backend.dto;

import com.hqa.backend.entity.enums.BoardType;
import java.time.OffsetDateTime;

public record PointEventResponse(String id, BoardType boardType, String reason, int points,
        String postId, OffsetDateTime createdAt, OffsetDateTime reversedAt) { }
