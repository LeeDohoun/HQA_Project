package com.hqa.backend.dto;

import com.hqa.backend.entity.enums.BoardType;
import com.hqa.backend.entity.enums.InquiryStatus;
import java.time.OffsetDateTime;

/**
 * 목록 전용 projection. content를 일부러 담지 않는다.
 * 엔티티를 그대로 로드하면 본문 TEXT까지 매번 읽히고 작성자 조회가 글마다 따라붙는다.
 */
public record PostSummary(
        String id,
        BoardType boardType,
        String title,
        String stockCode,
        int commentCount,
        InquiryStatus inquiryStatus,
        String authorId,
        String authorUserId,
        String authorFirstName,
        String authorLastName,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt,
        String authorNickname,
        int recommendationCount,
        int authorLevel,
        String authorTitle
) {
    public PostSummary(String id, BoardType boardType, String title, String stockCode, int commentCount,
            InquiryStatus inquiryStatus, String authorId, String authorUserId, String authorFirstName,
            String authorLastName, OffsetDateTime createdAt, OffsetDateTime updatedAt,
            String authorNickname, int recommendationCount) {
        this(id, boardType, title, stockCode, commentCount, inquiryStatus, authorId, authorUserId,
                authorFirstName, authorLastName, createdAt, updatedAt, authorNickname, recommendationCount, 0, null);
    }

    public PostSummary withProgress(BoardProgressResponse progress) {
        return new PostSummary(id, boardType, title, stockCode, commentCount, inquiryStatus, authorId,
                authorUserId, authorFirstName, authorLastName, createdAt, updatedAt, authorNickname,
                recommendationCount, progress == null ? 0 : progress.level(), progress == null ? null : progress.title());
    }
}
