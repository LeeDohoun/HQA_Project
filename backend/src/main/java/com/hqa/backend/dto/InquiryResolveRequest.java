package com.hqa.backend.dto;

import com.hqa.backend.entity.enums.InquiryStatus;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

public record InquiryResolveRequest(
        @NotNull InquiryStatus status,
        @Size(max = 20000) String adminReply,
        /** true면 문의가 가리키는 대상 글을 관리자 권한으로 함께 삭제한다. */
        boolean deleteTargetPost
) {
}
