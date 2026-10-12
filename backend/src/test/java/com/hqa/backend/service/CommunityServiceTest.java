package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

import com.hqa.backend.dto.*;
import com.hqa.backend.entity.*;
import com.hqa.backend.entity.enums.*;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.*;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.SliceImpl;
import org.springframework.data.domain.PageRequest;
import org.junit.jupiter.api.Test;

class CommunityServiceTest {
    private final PostRepository posts = mock(PostRepository.class);
    private final CommentRepository comments = mock(CommentRepository.class);
    private final CommunityService service = new CommunityService(posts, comments,
            mock(CommunityPointsService.class), mock(CommunityPointsRepository.class));
    private final User owner = user("owner", UserRole.user);

    @Test
    void commentCountUsesLockedPostState() {
        Post post = post(2);
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(post));
        when(comments.save(any(Comment.class))).thenAnswer(call -> {
            Comment comment = call.getArgument(0);
            comment.onCreate();
            return comment;
        });
        service.addComment(owner, "post", new CommentRequest("hello"));
        assertThat(post.getCommentCount()).isEqualTo(3);
        var order = inOrder(posts, comments);
        order.verify(posts).findByIdForUpdate("post");
        order.verify(comments).save(any(Comment.class));
    }

    @Test
    void cannotCommentOnPostDeletedWhileWaitingForLock() {
        Post post = post(0);
        post.setDeletedAt(OffsetDateTime.now());
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(post));
        assertThatThrownBy(() -> service.addComment(owner, "post", new CommentRequest("hello")))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(404));
        verifyNoInteractions(comments);
    }

    @Test
    void deletionRechecksLatestCommentCount() {
        Post post = post(3);
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(post));
        assertThatThrownBy(() -> service.delete(owner, "post"))
                .isInstanceOfSatisfying(ApiException.class,
                        e -> assertThat(e.getErrorCode()).isEqualTo(ErrorCode.POST_DELETE_BLOCKED));
        assertThat(post.isDeleted()).isFalse();
    }

    @Test
    void duplicateCommentDeletionDoesNotDecrementAgain() {
        Post post = post(2);
        Comment comment = comment(post);
        when(comments.findActivePostIdByCommentId("comment")).thenReturn(Optional.of("post"));
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(post));
        when(comments.findActiveById("comment")).thenReturn(Optional.of(comment), Optional.empty());
        service.deleteComment(owner, "comment");
        assertThatThrownBy(() -> service.deleteComment(owner, "comment"))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(404));
        assertThat(post.getCommentCount()).isEqualTo(1);
        assertThat(comment.isDeleted()).isTrue();
        var order = inOrder(posts, comments);
        order.verify(comments).findActivePostIdByCommentId("comment");
        order.verify(posts).findByIdForUpdate("post");
        order.verify(comments).findActiveById("comment");
    }

    @Test
    void unauthorizedCommentDeletionKeepsCount() {
        Post post = post(1);
        Comment comment = comment(post);
        when(comments.findActivePostIdByCommentId("comment")).thenReturn(Optional.of("post"));
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(post));
        when(comments.findActiveById("comment")).thenReturn(Optional.of(comment));
        assertThatThrownBy(() -> service.deleteComment(user("other", UserRole.user), "comment"))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(403));
        assertThat(post.getCommentCount()).isEqualTo(1);
        assertThat(comment.isDeleted()).isFalse();
    }

    @Test
    void ownCommentCanStillBeDeletedOnDeletedPost() {
        Post post = post(1);
        post.setDeletedAt(OffsetDateTime.now());
        when(comments.findActivePostIdByCommentId("comment")).thenReturn(Optional.of("post"));
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(post));
        when(comments.findActiveById("comment")).thenReturn(Optional.of(comment(post)));
        service.deleteComment(owner, "comment");
        assertThat(post.getCommentCount()).isZero();
    }

    @Test
    void postUpdatePreservesLatestCommentCount() {
        Post post = post(3);
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId(eq("post"), any(Pageable.class))).thenReturn(new SliceImpl<>(List.of()));
        var response = service.update(owner, "post", new PostUpdateRequest("edited", "body"));
        assertThat(response.commentCount()).isEqualTo(3);
        assertThat(post.getTitle()).isEqualTo("edited");
        verify(posts, never()).findActiveById(any());
    }

    @Test
    void adminResolutionLocksTargetBeforeDeletion() {
        User admin = user("admin", UserRole.admin);
        Post target = post(3);
        Post inquiry = post(0);
        inquiry.setId("inquiry");
        inquiry.setBoardType(BoardType.INQUIRY);
        inquiry.setInquiryStatus(InquiryStatus.OPEN);
        inquiry.setTargetPost(target);
        when(posts.findByIdForUpdate("inquiry")).thenReturn(Optional.of(inquiry));
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(target));
        when(comments.findActiveByPostId(eq("inquiry"), any(Pageable.class))).thenReturn(new SliceImpl<>(List.of()));
        service.resolveInquiry(admin, "inquiry", new InquiryResolveRequest(InquiryStatus.RESOLVED, "done", true));
        assertThat(target.isDeleted()).isTrue();
        assertThat(target.getDeletedBy()).isEqualTo("admin");
        assertThat(target.getCommentCount()).isEqualTo(3);
        var order = inOrder(posts);
        order.verify(posts).findByIdForUpdate("inquiry");
        order.verify(posts).findByIdForUpdate("post");
    }

    @Test
    void blockedOwnerCanEditAndRequestDeletion() {
        Post post = post(3);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId(eq("post"), any(Pageable.class))).thenReturn(new SliceImpl<>(List.of()));
        var response = service.get(owner, "post");
        assertThat(response.editable()).isTrue();
        assertThat(response.deletable()).isFalse();
        assertThat(response.deleteRequestable()).isTrue();
        assertThat(response.deleteBlockedReason()).isNotBlank();
        assertThat(response.inquiryResolvable()).isFalse();
        assertThat(response.targetPostDeletable()).isFalse();
    }

    @Test
    void otherReaderCannotEditOrRequestDeletion() {
        Post post = post(3);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId(eq("post"), any(Pageable.class))).thenReturn(new SliceImpl<>(List.of(comment(post))));
        var response = service.get(user("other", UserRole.user), "post");
        assertThat(response.editable()).isFalse();
        assertThat(response.deletable()).isFalse();
        assertThat(response.deleteRequestable()).isFalse();
        assertThat(response.deleteBlockedReason()).isNull();
        assertThat(response.comments().get(0).deletable()).isFalse();
    }

    @Test
    void adminCanDeleteOthersCommentsButCannotEditOthersPosts() {
        Post post = post(3);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId(eq("post"), any(Pageable.class))).thenReturn(new SliceImpl<>(List.of(comment(post))));
        var response = service.get(user("admin", UserRole.admin), "post");
        assertThat(response.editable()).isFalse();
        assertThat(response.deletable()).isTrue();
        assertThat(response.deleteRequestable()).isFalse();
        assertThat(response.comments().get(0).mine()).isFalse();
        assertThat(response.comments().get(0).deletable()).isTrue();
    }

    @Test
    void ownerCanDeleteOwnComment() {
        Post post = post(1);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId(eq("post"), any(Pageable.class))).thenReturn(new SliceImpl<>(List.of(comment(post))));
        var response = service.get(owner, "post");
        assertThat(response.comments().get(0).mine()).isTrue();
        assertThat(response.comments().get(0).deletable()).isTrue();
    }

    @Test
    void onlyAdminHasInquiryProcessingCapability() {
        Post target = post(3);
        Post inquiry = post(0);
        inquiry.setBoardType(BoardType.INQUIRY);
        inquiry.setInquiryStatus(InquiryStatus.OPEN);
        inquiry.setTargetPost(target);
        when(posts.findActiveById("post")).thenReturn(Optional.of(inquiry));
        when(comments.findActiveByPostId(eq("post"), any(Pageable.class))).thenReturn(new SliceImpl<>(List.of()));
        var ownerResponse = service.get(owner, "post");
        assertThat(ownerResponse.inquiryResolvable()).isFalse();
        assertThat(ownerResponse.targetPostDeletable()).isFalse();
        assertThat(ownerResponse.deleteRequestable()).isFalse();
        var adminResponse = service.get(user("admin", UserRole.admin), "post");
        assertThat(adminResponse.inquiryResolvable()).isTrue();
        assertThat(adminResponse.targetPostDeletable()).isTrue();
        target.setDeletedAt(OffsetDateTime.now());
        assertThat(service.get(user("admin", UserRole.admin), "post").targetPostDeletable()).isFalse();
    }

    @Test
    void anotherUserCannotReadInquiry() {
        Post inquiry = post(0);
        inquiry.setBoardType(BoardType.INQUIRY);
        when(posts.findActiveById("post")).thenReturn(Optional.of(inquiry));
        assertThatThrownBy(() -> service.get(user("other", UserRole.user), "post"))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(403));
        verifyNoInteractions(comments);
    }

    @Test
    void nonAdminCannotResolveEvenWhenCallingServiceDirectly() {
        assertThatThrownBy(() -> service.resolveInquiry(owner, "inquiry",
                new InquiryResolveRequest(InquiryStatus.RESOLVED, "answer", false)))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(403));
        verifyNoInteractions(posts, comments);
    }

    @Test
    void rejectedInquiryCannotDeleteTarget() {
        assertThatThrownBy(() -> service.resolveInquiry(user("admin", UserRole.admin), "inquiry",
                new InquiryResolveRequest(InquiryStatus.REJECTED, "answer", true)))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(400));
        verifyNoInteractions(posts, comments);
    }

    @Test
    void anotherUserCannotUpdatePost() {
        Post post = post(0);
        when(posts.findByIdForUpdate("post")).thenReturn(Optional.of(post));
        assertThatThrownBy(() -> service.update(user("other", UserRole.user), "post",
                new PostUpdateRequest("changed", "changed")))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(403));
        assertThat(post.getTitle()).isEqualTo("title");
        assertThat(post.getContent()).isEqualTo("body");
        verifyNoInteractions(comments);
    }

    @Test
    void detailOnlyLoadsFirstTwentyCommentsAndReturnsContinuation() {
        Post post = post(1000);
        List<Comment> first = java.util.stream.IntStream.range(0, 20).mapToObj(i -> comment(post)).toList();
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId("post", PageRequest.of(0, 20)))
                .thenReturn(new SliceImpl<>(first, PageRequest.of(0, 20), true));
        var response = service.get(owner, "post");
        assertThat(response.comments()).hasSize(20);
        assertThat(response.commentCount()).isEqualTo(1000);
        assertThat(response.hasMoreComments()).isTrue();
        assertThat(response.nextCommentCursor()).isEqualTo(first.get(19).getId());
        verify(comments, never()).countByPostIdAndDeletedAtIsNull(any());
    }

    @Test
    void deletedMarkerStillAllowsContinuationWithoutOffsetSkipping() {
        Post post = post(40);
        Comment marker = comment(post);
        marker.setDeletedAt(OffsetDateTime.now());
        Comment next = comment(post);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findByIdAndPostId(marker.getId(), "post")).thenReturn(Optional.of(marker));
        when(comments.findActiveAfter("post", marker.getCreatedAt(), marker.getId(), PageRequest.of(0, 20)))
                .thenReturn(new SliceImpl<>(List.of(next)));
        var response = service.listComments(owner, "post", marker.getId(), 20);
        assertThat(response.items()).extracting(CommentResponse::id).containsExactly(next.getId());
        assertThat(response.hasMore()).isFalse();
        assertThat(response.nextCursor()).isNull();
        assertThat(response.totalItems()).isEqualTo(40);
        verify(comments, never()).findActiveByPostId(any(), any());
    }

    @Test
    void commentBatchSizeIsCapped() {
        Post post = post(100);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId("post", PageRequest.of(0, 50))).thenReturn(new SliceImpl<>(List.of()));
        service.listComments(owner, "post", null, 1000);
        verify(comments).findActiveByPostId("post", PageRequest.of(0, 50));
    }

    @Test
    void nonPositiveCommentBatchSizeBecomesOne() {
        Post post = post(0);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId("post", PageRequest.of(0, 1))).thenReturn(new SliceImpl<>(List.of()));
        var response = service.listComments(owner, "post", null, 0);
        assertThat(response.items()).isEmpty();
        assertThat(response.nextCursor()).isNull();
        assertThat(response.hasMore()).isFalse();
    }

    @Test
    void cursorFromAnotherPostOrMissingCursorIsRejected() {
        Post post = post(40);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findByIdAndPostId("foreign", "post")).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.listComments(owner, "post", "foreign", 20))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(400));
        verify(comments, never()).findActiveAfter(any(), any(), any(), any());
    }

    @Test
    void inquiryCommentEndpointRejectsAnotherUserBeforeLookingUpCursor() {
        Post inquiry = post(40);
        inquiry.setBoardType(BoardType.INQUIRY);
        when(posts.findActiveById("post")).thenReturn(Optional.of(inquiry));
        assertThatThrownBy(() -> service.listComments(user("other", UserRole.user), "post", "cursor", 20))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(403));
        verifyNoInteractions(comments);
    }

    @Test
    void deletedPostDoesNotExposeComments() {
        when(posts.findActiveById("post")).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.listComments(owner, "post", null, 20))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(404));
        verifyNoInteractions(comments);
    }

    private Post post(int count) {
        Post post = new Post();
        post.setId("post");
        post.setUser(owner);
        post.setBoardType(BoardType.FREE);
        post.setTitle("title");
        post.setContent("body");
        post.setCommentCount(count);
        post.onCreate();
        return post;
    }

    private Comment comment(Post post) {
        Comment comment = new Comment();
        comment.setPost(post);
        comment.setUser(owner);
        comment.setContent("hello");
        comment.onCreate();
        return comment;
    }

    private static User user(String id, UserRole role) {
        User user = mock(User.class);
        when(user.getId()).thenReturn(id);
        when(user.getUserId()).thenReturn(id);
        when(user.getRole()).thenReturn(role);
        return user;
    }
}
