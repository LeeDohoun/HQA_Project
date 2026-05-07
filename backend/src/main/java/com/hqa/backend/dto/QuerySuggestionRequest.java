package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;

public class QuerySuggestionRequest {

    @NotBlank
    private String query;

    public String getQuery() {
        return query;
    }

    public void setQuery(String query) {
        this.query = query;
    }
}
