package com.hqa.backend.service;

import com.hqa.backend.dto.BoardProgressResponse;
import com.hqa.backend.dto.MyProfileResponse;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.NicknameRequest;
import com.hqa.backend.entity.Comment;
import com.hqa.backend.entity.Post;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.enums.BoardType;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.CommunityPointsRepository;
import com.hqa.backend.repository.CommunityPointsRepository.Totals;
import com.hqa.backend.repository.UserRepository;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional
public class CommunityPointsService {
    public static final int POST_POINTS = 10;
    public static final int COMMENT_POINTS = 2;
    public static final int RECOMMENDATION_POINTS = 5;
    private static final long[] FREE_LEVELS = {0, 50, 150, 300, 600, 1000};
    private static final long[] STOCK_LEVELS = {0, 100, 300, 600, 1200, 2000};
    private static final String[] FREE_TITLES = {"새싹", "이야기꾼", "소통지기", "인기 논객", "커뮤니티 리더", "소통의 전설"};
    private static final String[] STOCK_TITLES = {"시장 관찰자", "종목 탐험가", "투자 연구원", "분석 전문가", "시장 전략가", "투자 현자"};
    private static final Totals EMPTY = new Totals(0, 0, 0, 0);
    private final CommunityPointsRepository points;
    private final UserRepository users;

    public CommunityPointsService(CommunityPointsRepository points, UserRepository users) {
        this.points = points;
        this.users = users;
    }

    public void awardPost(Post post) {
        if (post.getBoardType() != BoardType.INQUIRY)
            points.award(post.getUser().getId(), post.getBoardType(), "POST_CREATED", POST_POINTS, post.getId(), null, null);
    }

    public void awardComment(Comment comment) {
        Post post = comment.getPost();
        if (post.getBoardType() != BoardType.INQUIRY)
            points.award(comment.getUser().getId(), post.getBoardType(), "COMMENT_CREATED", COMMENT_POINTS, post.getId(), comment.getId(), null);
    }

    public void awardRecommendation(Post post, String recommendationId) {
        points.award(post.getUser().getId(), post.getBoardType(), "RECOMMEND_RECEIVED", RECOMMENDATION_POINTS, post.getId(), null, recommendationId);
    }

    public void reversePost(Post post) { if (post.getBoardType() != BoardType.INQUIRY) points.reversePost(post.getId()); }
    public void reverseComment(String commentId) { points.reverseComment(commentId); }

    @Transactional(readOnly = true)
    public Map<String, BoardProgressResponse> progress(Collection<String> userIds, BoardType board) {
        if (board == BoardType.INQUIRY) return Map.of();
        Map<String, Totals> totals = points.totals(userIds, board);
        Map<String, BoardProgressResponse> result = new java.util.HashMap<>();
        for (String userId : userIds) result.put(userId, level(board, totals.getOrDefault(userId, EMPTY)));
        return result;
    }

    public static BoardProgressResponse level(BoardType board, Totals totals) {
        if (board == BoardType.INQUIRY) throw new IllegalArgumentException("Inquiries have no points or levels");
        long[] thresholds = board == BoardType.FREE ? FREE_LEVELS : STOCK_LEVELS;
        String[] titles = board == BoardType.FREE ? FREE_TITLES : STOCK_TITLES;
        int index = 0;
        while (index + 1 < thresholds.length && totals.points() >= thresholds[index + 1]) index++;
        Long next = index + 1 < thresholds.length ? thresholds[index + 1] : null;
        return new BoardProgressResponse(board, totals.points(), index + 1, titles[index], thresholds[index], next,
                next == null ? 0 : next - totals.points(), totals.posts(), totals.comments(), totals.recommendations());
    }

    @Transactional(readOnly = true)
    public MyProfileResponse profile(User user) {
        var ids = List.of(user.getId());
        var free = progress(ids, BoardType.FREE).get(user.getId());
        var stock = progress(ids, BoardType.STOCK).get(user.getId());
        return new MyProfileResponse(user.getId(), user.getUserId(), user.getNickname(), free.points() + stock.points(),
                user.getCreatedAt(), List.of(free, stock), points.recent(user.getId()));
    }

    public MyProfileResponse updateNickname(User viewer, NicknameRequest request) {
        String nickname = request.nickname().strip();
        if (nickname.length() < 2 || nickname.length() > 30)
            throw new ApiException(ErrorCode.INVALID_REQUEST, 400, "Nickname must contain 2 to 30 characters", null);
        User user = users.lockByUserId(viewer.getUserId())
                .orElseThrow(() -> new ApiException(ErrorCode.UNAUTHORIZED, 401, "Login required", null));
        if (!user.isActive()) throw new ApiException(ErrorCode.USER_INACTIVE, 403, "Inactive user", null);
        user.setNickname(nickname);
        try { users.flush(); }
        catch (DataIntegrityViolationException error) {
            if (error.getMostSpecificCause() instanceof java.sql.SQLException sql && "23505".equals(sql.getSQLState()))
                throw new ApiException(ErrorCode.NICKNAME_ALREADY_EXISTS, 409, "이미 사용 중인 닉네임입니다.", null);
            throw error;
        }
        return profile(user);
    }
}
