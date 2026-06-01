package com.hqa.backend.dto;

public record InternalTradeSignalResponse(String signalId, String status, boolean deduplicated) {
}
