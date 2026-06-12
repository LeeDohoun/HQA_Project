package com.hqa.backend.dto;

import java.util.List;

public record WatchlistResponse(List<WatchlistItemResponse> items, int total) {
}
