package com.hqa.backend.service;

import com.hqa.backend.dto.AuthorResponse;
import com.hqa.backend.dto.BoardProgressResponse;
import com.hqa.backend.dto.CommentListResponse;
import com.hqa.backend.dto.CommentRequest;
import com.hqa.backend.dto.CommentResponse;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.InquiryCreateRequest;
import com.hqa.backend.dto.InquiryResolveRequest;
import com.hqa.backend.dto.PostCreateRequest;
import com.hqa.backend.dto.PostListResponse;
import com.hqa.backend.dto.PostResponse;
import com.hqa.backend.dto.PostSummary;
import com.hqa.backend.dto.PostUpdateRequest;
import com.hqa.backend.entity.Comment;
import com.hqa.backend.entity.Post;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.enums.BoardType;
import com.hqa.backend.entity.enums.InquiryStatus;
import com.hqa.backend.entity.enums.UserRole;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.CommentRepository;
import com.hqa.backend.repository.CommunityPointsRepository;
import com.hqa.backend.repository.PostRepository;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Slice;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional
public class CommunityService {

    /** 작성 후 이 시간이 지나면 본인도 지울 수 없다. */
    static final Duration DELETE_WINDOW = Duration.ofHours(1);
    /** 살아있는 댓글이 이만큼 쌓이면 본인도 지울 수 없다. */
    static final int DELETE_BLOCKING_COMMENTS = 3;

    private static final int MAX_PAGE_SIZE = 50;
    private static final int INITIAL_COMMENT_SIZE = 20;

    private final PostRepository postRepository;
    private final CommentRepository commentRepository;
    private final CommunityPointsService points;
    private final CommunityPointsRepository recommendations;

    public CommunityService(PostRepository postRepository, CommentRepository commentRepository,
                            CommunityPointsService points, CommunityPointsRepository recommendations) {
        this.postRepository = postRepository;
        this.commentRepository = commentRepository;
        this.points = points;
        this.recommendations = recommendations;
    }

    // 목록

    @Transactional(readOnly = true)
    public PostListResponse listFree(int page, int size) {
        return toListResponse(postRepository.findFreeBoard(pageable(page, size)));
    }

    @Transactional(readOnly = true)
    public PostListResponse listStock(String stockCode, int page, int size) {
        String code = (stockCode == null || stockCode.isBlank()) ? null : stockCode.trim();
        return toListResponse(postRepository.findStockBoard(code, pageable(page, size)));
    }

    /** 문의 목록은 관리자면 전체, 아니면 본인 것만 돌려준다. */
    @Transactional(readOnly = true)
    public PostListResponse listInquiries(User viewer, int page, int size) {
        Pageable pageable = pageable(page, size);
        Page<PostSummary> result = isAdmin(viewer)
                ? postRepository.findAllInquiriesForAdmin(pageable)
                : postRepository.findMyInquiries(viewer.getId(), pageable);
        return toListResponse(result);
    }

    // 상세

    @Transactional(readOnly = true)
    public PostResponse get(User viewer, String postId) {
        Post post = requireReadablePost(viewer, postId);
        return toPostResponse(viewer, post, readComments(viewer, post, null, INITIAL_COMMENT_SIZE));
    }

    // 작성

    public PostResponse createFree(User author, PostCreateRequest request) {
        if (request.stockCode() != null && !request.stockCode().isBlank()) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400,
                    "Free board posts cannot carry a stock code", null);
        }
        Post post = newPost(author, BoardType.FREE, request.title(), request.content());
        return savePublicPost(author, post);
    }

    public PostResponse createStock(User author, PostCreateRequest request) {
        if (request.stockCode() == null || request.stockCode().isBlank()) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400,
                    "Stock board posts require a stock code", null);
        }
        Post post = newPost(author, BoardType.STOCK, request.title(), request.content());
        post.setStockCode(request.stockCode().trim());
        return savePublicPost(author, post);
    }

    public PostResponse createInquiry(User author, InquiryCreateRequest request) {
        Post post = newPost(author, BoardType.INQUIRY, request.title(), request.content());
        post.setInquiryStatus(InquiryStatus.OPEN);
        if (request.targetPostId() != null && !request.targetPostId().isBlank()) {
            Post target = requireActivePost(request.targetPostId().trim());
            // 삭제 요청은 본인 글에 대해서만 받는다. 남의 글을 지워달라는 건 신고이지 삭제 요청이 아니다.
            if (!isOwner(author, target)) {
                throw new ApiException(ErrorCode.INQUIRY_INVALID_TARGET, 400,
                        "Delete requests are only accepted for your own posts", null);
            }
            if (target.getBoardType() == BoardType.INQUIRY) {
                throw new ApiException(ErrorCode.INQUIRY_INVALID_TARGET, 400,
                        "Inquiries cannot target another inquiry", null);
            }
            post.setTargetPost(target);
        }
        return toPostResponse(author, postRepository.save(post), emptyComments());
    }

    // 수정

    private PostResponse savePublicPost(User author, Post post) {
        Post saved = postRepository.save(post);
        postRepository.flush();
        points.awardPost(saved);
        return toPostResponse(author, saved, emptyComments());
    }

    /** Recommendations are permanent, session-bound, and serialized with every other post mutation. */
    public PostResponse recommend(User viewer, String postId) {
        Post post = requireActivePostForUpdate(postId);
        if (post.getBoardType() == BoardType.INQUIRY || isOwner(viewer, post))
            throw new ApiException(ErrorCode.RECOMMENDATION_NOT_ALLOWED, 400, "자기 글이나 문의글은 추천할 수 없습니다.", null);
        String recommendationId = UUID.randomUUID().toString();
        if (!recommendations.addRecommendation(recommendationId, postId, viewer.getId()))
            throw new ApiException(ErrorCode.RECOMMENDATION_ALREADY_EXISTS, 409, "이미 추천한 글입니다. 추천은 취소할 수 없습니다.", null);
        post.setRecommendationCount(post.getRecommendationCount() + 1);
        postRepository.flush();
        points.awardRecommendation(post, recommendationId);
        return toPostResponse(viewer, post, readComments(viewer, post, null, INITIAL_COMMENT_SIZE));
    }

    public PostResponse update(User editor, String postId, PostUpdateRequest request) {
        Post post = requireActivePostForUpdate(postId);
        if (!isOwner(editor, post)) {
            throw new ApiException(ErrorCode.POST_FORBIDDEN, 403, "Not your post", null);
        }
        post.setTitle(request.title().trim());
        post.setContent(request.content());
        // Populate @PreUpdate timestamps before copying them into the response.
        postRepository.flush();
        return toPostResponse(editor, post, readComments(editor, post, null, INITIAL_COMMENT_SIZE));
    }

    // 삭제

    public void delete(User requester, String postId) {
        Post post = requireActivePostForUpdate(postId);
        if (!isOwner(requester, post) && !isAdmin(requester)) {
            throw new ApiException(ErrorCode.POST_FORBIDDEN, 403, "Not your post", null);
        }
        String blocked = deleteBlockedReason(requester, post);
        if (blocked != null) {
            throw new ApiException(ErrorCode.POST_DELETE_BLOCKED, 409, blocked,
                    "Submit an inquiry to ask an administrator to remove this post");
        }
        softDelete(post, requester);
    }

    /**
     * 삭제를 막는 이유를 돌려준다. 지울 수 있으면 null.
     * 관리자는 언제나 지울 수 있고, 문의글은 제한 대상이 아니다 -
     * 문의를 못 지우면 잘못 쓴 문의를 지우려고 또 문의를 써야 한다.
     */
    private String deleteBlockedReason(User requester, Post post) {
        if (isAdmin(requester)) {
            return null;
        }
        if (post.getBoardType() == BoardType.INQUIRY) {
            return null;
        }
        if (post.getCommentCount() >= DELETE_BLOCKING_COMMENTS) {
            return "Posts with %d or more comments cannot be deleted".formatted(DELETE_BLOCKING_COMMENTS);
        }
        if (OffsetDateTime.now().isAfter(post.getCreatedAt().plus(DELETE_WINDOW))) {
            return "Posts can only be deleted within %d hour of writing".formatted(DELETE_WINDOW.toHours());
        }
        return null;
    }

    // 댓글

    @Transactional(readOnly = true)
    public CommentListResponse listComments(User viewer, String postId, String after, int size) {
        Post post = requireReadablePost(viewer, postId);
        return readComments(viewer, post, after, size);
    }

    @Transactional(readOnly = true)
    public CommentListResponse visibleComments(User viewer, String postId, List<String> ids) {
        Post post = requireReadablePost(viewer, postId);
        if (ids == null || ids.isEmpty() || ids.size() > MAX_PAGE_SIZE) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "Between 1 and 50 comment IDs are required", null);
        }
        List<Comment> comments = commentRepository.findActiveByPostIdAndIds(postId, ids.stream().distinct().toList());
        Map<String, BoardProgressResponse> ranks = points.progress(comments.stream().map(c -> c.getUser().getId()).distinct().toList(), post.getBoardType());
        List<CommentResponse> items = comments.stream().map(comment -> toCommentResponse(viewer, comment, ranks.get(comment.getUser().getId()))).toList();
        return new CommentListResponse(items, null, false, post.getCommentCount());
    }

    public CommentResponse addComment(User author, String postId, CommentRequest request) {
        Post post = requireActivePostForUpdate(postId);
        if (post.getBoardType() == BoardType.INQUIRY && !isOwner(author, post) && !isAdmin(author)) {
            throw new ApiException(ErrorCode.POST_FORBIDDEN, 403, "Not allowed to comment on this inquiry", null);
        }
        Comment comment = new Comment();
        comment.setPost(post);
        comment.setUser(author);
        comment.setContent(request.content());
        Comment saved = commentRepository.save(comment);
        post.setCommentCount(post.getCommentCount() + 1);
        commentRepository.flush();
        points.awardComment(saved);
        return toCommentResponse(author, saved, points.progress(List.of(author.getId()), post.getBoardType()).get(author.getId()));
    }

    public void deleteComment(User requester, String commentId) {
        String postId = commentRepository.findActivePostIdByCommentId(commentId)
                .orElseThrow(() -> new ApiException(ErrorCode.COMMENT_NOT_FOUND, 404, "Comment not found", null));
        // Lock the parent first, then recheck the comment after any competing deletion.
        // Preserve the ability to delete own comments on a soft-deleted post.
        Post post = postRepository.findByIdForUpdate(postId)
                .orElseThrow(() -> new ApiException(ErrorCode.POST_NOT_FOUND, 404, "Post not found", null));
        Comment comment = commentRepository.findActiveById(commentId)
                .orElseThrow(() -> new ApiException(ErrorCode.COMMENT_NOT_FOUND, 404, "Comment not found", null));
        if (!comment.getUser().getId().equals(requester.getId()) && !isAdmin(requester)) {
            throw new ApiException(ErrorCode.POST_FORBIDDEN, 403, "Not your comment", null);
        }
        comment.setDeletedAt(OffsetDateTime.now());
        comment.setDeletedBy(requester.getId());
        post.setCommentCount(post.getCommentCount() - 1);
        points.reverseComment(commentId);
    }

    // 관리자

    /** 문의를 종결한다. 삭제 요청이면 대상 글을 함께 지울 수 있다. */
    public PostResponse resolveInquiry(User admin, String inquiryId, InquiryResolveRequest request) {
        if (!isAdmin(admin)) {
            throw new ApiException(ErrorCode.FORBIDDEN, 403, "Admin only", null);
        }
        if (request.deleteTargetPost() && request.status() != InquiryStatus.RESOLVED) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400,
                    "Only resolved inquiries can delete the target post", null);
        }
        Post inquiry = requireActivePostForUpdate(inquiryId);
        if (inquiry.getBoardType() != BoardType.INQUIRY) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "Not an inquiry", null);
        }
        if (request.status() == InquiryStatus.OPEN) {
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "Cannot resolve back to OPEN", null);
        }
        if (request.deleteTargetPost()) {
            Post target = inquiry.getTargetPost();
            if (target == null) {
                throw new ApiException(ErrorCode.INQUIRY_INVALID_TARGET, 400,
                        "This inquiry has no deletable target post", null);
            }
            target = postRepository.findByIdForUpdate(target.getId())
                    .orElseThrow(() -> new ApiException(ErrorCode.INQUIRY_INVALID_TARGET, 400,
                            "This inquiry has no deletable target post", null));
            if (target.isDeleted()) {
                throw new ApiException(ErrorCode.INQUIRY_INVALID_TARGET, 400,
                        "This inquiry has no deletable target post", null);
            }
            softDelete(target, admin);
        }
        inquiry.setInquiryStatus(request.status());
        inquiry.setAdminReply(request.adminReply());
        postRepository.flush();
        return toPostResponse(admin, inquiry, readComments(admin, inquiry, null, INITIAL_COMMENT_SIZE));
    }

    // 내부

    private Post newPost(User author, BoardType type, String title, String content) {
        Post post = new Post();
        post.setBoardType(type);
        post.setUser(author);
        post.setTitle(title.trim());
        post.setContent(content);
        return post;
    }

    private void softDelete(Post post, User actor) {
        post.setDeletedAt(OffsetDateTime.now());
        post.setDeletedBy(actor.getId());
        points.reversePost(post);
    }

    private Post requireReadablePost(User viewer, String postId) {
        Post post = requireActivePost(postId);
        if (post.getBoardType() == BoardType.INQUIRY && !isOwner(viewer, post) && !isAdmin(viewer)) {
            throw new ApiException(ErrorCode.POST_FORBIDDEN, 403, "Not allowed to read this inquiry", null);
        }
        return post;
    }

    private CommentListResponse readComments(User viewer, Post post, String after, int size) {
        Pageable limit = pageable(0, size);
        Slice<Comment> slice;
        if (after == null || after.isBlank()) {
            slice = commentRepository.findActiveByPostId(post.getId(), limit);
        } else {
            Comment marker = commentRepository.findByIdAndPostId(after, post.getId())
                    .orElseThrow(() -> new ApiException(ErrorCode.INVALID_REQUEST, 400, "Invalid comment cursor", null));
            slice = commentRepository.findActiveAfter(post.getId(), marker.getCreatedAt(), marker.getId(), limit);
        }
        Map<String, BoardProgressResponse> ranks = points.progress(slice.stream().map(c -> c.getUser().getId()).distinct().toList(), post.getBoardType());
        List<CommentResponse> items = slice.stream().map(c -> toCommentResponse(viewer, c, ranks.get(c.getUser().getId()))).toList();
        String nextCursor = slice.hasNext() && !items.isEmpty() ? items.get(items.size() - 1).id() : null;
        return new CommentListResponse(items, nextCursor, slice.hasNext(), post.getCommentCount());
    }

    private CommentListResponse emptyComments() {
        return new CommentListResponse(List.of(), null, false, 0);
    }

    private Post requireActivePost(String postId) {
        return postRepository.findActiveById(postId)
                .orElseThrow(() -> new ApiException(ErrorCode.POST_NOT_FOUND, 404, "Post not found", null));
    }

    private Post requireActivePostForUpdate(String postId) {
        Post post = postRepository.findByIdForUpdate(postId)
                .orElseThrow(() -> new ApiException(ErrorCode.POST_NOT_FOUND, 404, "Post not found", null));
        if (post.isDeleted()) {
            throw new ApiException(ErrorCode.POST_NOT_FOUND, 404, "Post not found", null);
        }
        return post;
    }

    private static boolean isAdmin(User user) {
        return user != null && user.getRole() == UserRole.admin;
    }

    private static boolean isOwner(User user, Post post) {
        return user != null && post.getUser().getId().equals(user.getId());
    }

    private Pageable pageable(int page, int size) {
        int safePage = Math.max(page, 0);
        int safeSize = Math.min(Math.max(size, 1), MAX_PAGE_SIZE);
        return PageRequest.of(safePage, safeSize);
    }

    private PostListResponse toListResponse(Page<PostSummary> page) {
        BoardType board = page.hasContent() ? page.getContent().get(0).boardType() : BoardType.INQUIRY;
        Map<String, BoardProgressResponse> ranks = points.progress(page.stream().map(PostSummary::authorId).distinct().toList(), board);
        return new PostListResponse(
                page.stream().map(post -> post.withProgress(ranks.get(post.authorId()))).toList(),
                page.getNumber(),
                page.getSize(),
                page.getTotalElements(),
                page.getTotalPages()
        );
    }

    private PostResponse toPostResponse(User viewer, Post post, CommentListResponse comments) {
        boolean owner = isOwner(viewer, post);
        boolean admin = isAdmin(viewer);
        String blocked = owner ? deleteBlockedReason(viewer, post) : null;
        boolean canDelete = (owner || admin) && blocked == null;
        boolean inquiry = post.getBoardType() == BoardType.INQUIRY;
        boolean canDeleteTarget = admin && inquiry && post.getTargetPost() != null
                && !post.getTargetPost().isDeleted();
        boolean recommended = !inquiry && viewer != null && recommendations.recommended(post.getId(), viewer.getId());
        BoardProgressResponse authorProgress = points.progress(List.of(post.getUser().getId()), post.getBoardType()).get(post.getUser().getId());
        return new PostResponse(
                post.getId(),
                post.getBoardType(),
                post.getTitle(),
                post.getContent(),
                post.getStockCode(),
                post.getCommentCount(),
                post.getRecommendationCount(),
                recommended,
                !inquiry && viewer != null && !owner && !recommended,
                toAuthorResponse(post.getUser(), authorProgress),
                canDelete,
                owner,
                owner && !inquiry && blocked != null,
                admin && inquiry,
                canDeleteTarget,
                canDelete ? null : blocked,
                post.getInquiryStatus(),
                post.getAdminReply(),
                post.getTargetPost() == null ? null : post.getTargetPost().getId(),
                comments.items(),
                comments.nextCursor(),
                comments.hasMore(),
                post.getCreatedAt(),
                post.getUpdatedAt()
        );
    }

    private CommentResponse toCommentResponse(User viewer, Comment comment, BoardProgressResponse progress) {
        return new CommentResponse(
                comment.getId(),
                comment.getContent(),
                toAuthorResponse(comment.getUser(), progress),
                viewer != null && comment.getUser().getId().equals(viewer.getId()),
                viewer != null && (comment.getUser().getId().equals(viewer.getId()) || isAdmin(viewer)),
                comment.getCreatedAt(),
                comment.getUpdatedAt()
        );
    }

    private static AuthorResponse toAuthorResponse(User user, BoardProgressResponse progress) {
        return new AuthorResponse(user.getId(), user.getUserId(), user.getFirstName(), user.getLastName(),
                user.getNickname(), progress == null ? 0 : progress.level(), progress == null ? null : progress.title());
    }
}
