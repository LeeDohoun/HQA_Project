package com.hqa.backend.entity;

import com.hqa.backend.entity.enums.BoardType;
import com.hqa.backend.entity.enums.InquiryStatus;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.time.temporal.ChronoUnit;
import java.util.UUID;

@Entity
@Table(name = "posts")
public class Post {

    @Id
    private String id;

    @Enumerated(EnumType.STRING)
    @Column(name = "board_type", nullable = false, length = 16)
    private BoardType boardType;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(nullable = false, length = 160)
    private String title;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;

    @Column(name = "stock_code", length = 12)
    private String stockCode;

    @Column(name = "comment_count", nullable = false)
    private int commentCount;

    @Column(name = "recommendation_count", nullable = false)
    private int recommendationCount;

    public int getRecommendationCount() { return recommendationCount; }
    public void setRecommendationCount(int count) { recommendationCount = count; }

    /** 삭제 요청 문의글이 가리키는 대상 글. */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "target_post_id")
    private Post targetPost;

    @Enumerated(EnumType.STRING)
    @Column(name = "inquiry_status", length = 16)
    private InquiryStatus inquiryStatus;

    @Column(name = "admin_reply", columnDefinition = "TEXT")
    private String adminReply;

    @Column(name = "deleted_at")
    private OffsetDateTime deletedAt;

    @Column(name = "deleted_by")
    private String deletedBy;

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;

    @PrePersist
    public void onCreate() {
        if (id == null) {
            id = UUID.randomUUID().toString();
        }
        OffsetDateTime now = OffsetDateTime.now().truncatedTo(ChronoUnit.MICROS);
        createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    public void onUpdate() {
        updatedAt = OffsetDateTime.now().truncatedTo(ChronoUnit.MICROS);
    }

    public boolean isDeleted() {
        return deletedAt != null;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public BoardType getBoardType() {
        return boardType;
    }

    public void setBoardType(BoardType boardType) {
        this.boardType = boardType;
    }

    public User getUser() {
        return user;
    }

    public void setUser(User user) {
        this.user = user;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getContent() {
        return content;
    }

    public void setContent(String content) {
        this.content = content;
    }

    public String getStockCode() {
        return stockCode;
    }

    public void setStockCode(String stockCode) {
        this.stockCode = stockCode;
    }

    public int getCommentCount() {
        return commentCount;
    }

    public void setCommentCount(int commentCount) {
        this.commentCount = commentCount;
    }

    public Post getTargetPost() {
        return targetPost;
    }

    public void setTargetPost(Post targetPost) {
        this.targetPost = targetPost;
    }

    public InquiryStatus getInquiryStatus() {
        return inquiryStatus;
    }

    public void setInquiryStatus(InquiryStatus inquiryStatus) {
        this.inquiryStatus = inquiryStatus;
    }

    public String getAdminReply() {
        return adminReply;
    }

    public void setAdminReply(String adminReply) {
        this.adminReply = adminReply;
    }

    public OffsetDateTime getDeletedAt() {
        return deletedAt;
    }

    public void setDeletedAt(OffsetDateTime deletedAt) {
        this.deletedAt = deletedAt;
    }

    public String getDeletedBy() {
        return deletedBy;
    }

    public void setDeletedBy(String deletedBy) {
        this.deletedBy = deletedBy;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }
}
