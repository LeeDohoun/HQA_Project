package com.hqa.backend.dto;

public record ErrorResponse(
        boolean success,
        String errorCode,
        String message,
        String detail
) {
    public static ErrorResponse of(ErrorCode errorCode, String message, String detail) {
        return new ErrorResponse(false, errorCode.name(), message, detail);
    }
}
