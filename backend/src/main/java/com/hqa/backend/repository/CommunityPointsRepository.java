package com.hqa.backend.repository;

import com.hqa.backend.dto.PointEventResponse;
import com.hqa.backend.entity.enums.BoardType;
import java.time.OffsetDateTime;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class CommunityPointsRepository {
    public record Totals(long points, long posts, long comments, long recommendations) { }
    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate named;

    public CommunityPointsRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
        this.named = new NamedParameterJdbcTemplate(jdbc);
    }

    public void award(String userId, BoardType board, String reason, int points, String postId,
                      String commentId, String recommendationId) {
        jdbc.update("""
            insert into community_point_events(id, user_id, board_type, reason, points, post_id, comment_id, recommendation_id)
            values (?, ?, ?, ?, ?, ?, ?, ?) on conflict do nothing
            """, UUID.randomUUID().toString(), userId, board.name(), reason, points, postId, commentId, recommendationId);
    }

    public Map<String, Totals> totals(Collection<String> users, BoardType board) {
        if (users.isEmpty() || board == BoardType.INQUIRY) return Map.of();
        return named.query("""
            select user_id, sum(points) as points,
              count(*) filter (where reason='POST_CREATED') as posts,
              count(*) filter (where reason='COMMENT_CREATED') as comments,
              count(*) filter (where reason='RECOMMEND_RECEIVED') as recommendations
            from community_point_events where user_id in (:users) and board_type=:board and reversed_at is null
            group by user_id
            """, Map.of("users", users, "board", board.name()), rs -> {
                Map<String, Totals> totals = new java.util.HashMap<>();
                while (rs.next()) totals.put(rs.getString("user_id"), new Totals(rs.getLong("points"),
                        rs.getLong("posts"), rs.getLong("comments"), rs.getLong("recommendations")));
                return totals;
            });
    }

    public List<PointEventResponse> recent(String userId) {
        return jdbc.query("""
            select id, board_type, reason, points, post_id, created_at, reversed_at
            from community_point_events where user_id=? order by created_at desc, id desc limit 20
            """, (rs, row) -> new PointEventResponse(rs.getString("id"), BoardType.valueOf(rs.getString("board_type")),
                rs.getString("reason"), rs.getInt("points"), rs.getString("post_id"),
                rs.getObject("created_at", OffsetDateTime.class), rs.getObject("reversed_at", OffsetDateTime.class)), userId);
    }

    public void reversePost(String postId) {
        jdbc.update("update community_point_events set reversed_at=current_timestamp where post_id=? and reversed_at is null", postId);
    }

    public void reverseComment(String commentId) {
        jdbc.update("update community_point_events set reversed_at=current_timestamp where comment_id=? and reversed_at is null", commentId);
    }

    public boolean recommended(String postId, String userId) {
        return Boolean.TRUE.equals(jdbc.queryForObject("select exists(select 1 from post_recommendations where post_id=? and user_id=?)", Boolean.class, postId, userId));
    }

    public boolean addRecommendation(String id, String postId, String userId) {
        return jdbc.update("insert into post_recommendations(id, post_id, user_id) values (?, ?, ?) on conflict (post_id, user_id) do nothing", id, postId, userId) == 1;
    }
}
