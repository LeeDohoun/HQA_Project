package com.hqa.backend.exception;

import com.hqa.backend.dto.ErrorCode;

public class ApiException extends RuntimeException {

    private final ErrorCode errorCode;
    private final int status;
    private final String detail;

    public ApiException(ErrorCode errorCode, int status, String message, String detail) {
        super(message);
        this.errorCode = errorCode;
        this.status = status;
        this.detail = detail;
    }

    public ErrorCode getErrorCode() {
        return errorCode;
    }

    public int getStatus() {
        return status;
    }

    public String getDetail() {
        return detail;
    }
}
