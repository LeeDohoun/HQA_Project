package com.hqa.backend.dto;

import com.hqa.backend.entity.enums.BoardType;
import com.hqa.backend.entity.enums.InquiryStatus;
import java.time.OffsetDateTime;
import java.util.List;

public record PostResponse(
        String id,
        BoardType boardType,
        String title,
        String content,
        String stockCode,
        int commentCount,
        int recommendationCount,
        boolean recommended,
        boolean recommendable,
        AuthorResponse author,
        /** 지금 이 요청자가 삭제할 수 있는지. 프론트에서 버튼 노출 판단에 쓴다. */
        boolean deletable,
        boolean editable,
        boolean deleteRequestable,
        boolean inquiryResolvable,
        boolean targetPostDeletable,
        /** 삭제할 수 없다면 그 이유. 삭제 가능하면 null. */
        String deleteBlockedReason,
        InquiryStatus inquiryStatus,
        String adminReply,
        String targetPostId,
        List<CommentResponse> comments,
        String nextCommentCursor,
        boolean hasMoreComments,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {
}
