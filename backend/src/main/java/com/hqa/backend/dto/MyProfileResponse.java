package com.hqa.backend.dto;

import java.time.OffsetDateTime;
import java.util.List;

public record MyProfileResponse(String id, String userId, String nickname, long totalPoints,
        OffsetDateTime createdAt, List<BoardProgressResponse> boards, List<PointEventResponse> recentEvents) { }
