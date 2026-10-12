package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record InquiryCreateRequest(
        @NotBlank @Size(max = 160) String title,
        @NotBlank @Size(max = 20000) String content,
        /** 삭제 요청이면 대상 글 id. 일반 문의면 비워 둔다. */
        String targetPostId
) {
}
