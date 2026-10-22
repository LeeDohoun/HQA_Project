package com.hqa.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record PostCreateRequest(
        @NotBlank @Size(max = 160) String title,
        @NotBlank @Size(max = 20000) String content,
        /** 종목토론방에서만 사용한다. 다른 게시판에 넣으면 거부된다. */
        @Size(max = 12) String stockCode
) {
}
