package com.hqa.backend.dto;

import java.util.List;

public record QuerySuggestionResponse(
        String originalQuery,
        boolean isAnswerable,
        String correctedQuery,
        List<String> suggestions,
        String reason
) {
}
