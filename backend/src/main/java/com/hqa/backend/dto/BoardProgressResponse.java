package com.hqa.backend.dto;

import com.hqa.backend.entity.enums.BoardType;

public record BoardProgressResponse(BoardType boardType, long points, int level, String title,
        long currentLevelPoints, Long nextLevelPoints, long pointsToNextLevel,
        long postCount, long commentCount, long recommendationsReceived) { }
