package com.hqa.backend.dto;

import java.util.Map;

public record ScoreDetail(
        String agent,
        double totalScore,
        double maxScore,
        String grade,
        String opinion,
        Map<String, Object> details
) {
}
