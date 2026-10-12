package com.hqa.backend.repository;

import com.hqa.backend.entity.Comment;
import java.time.OffsetDateTime;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Slice;
import java.util.Optional;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface CommentRepository extends JpaRepository<Comment, String> {

    /** 상세 화면의 댓글 목록. 작성자를 함께 가져와 N+1을 막는다. */
    @Query("""
            select c from Comment c join fetch c.user
            where c.post.id = :postId and c.deletedAt is null
            order by c.createdAt asc, c.id asc
            """)
    Slice<Comment> findActiveByPostId(@Param("postId") String postId, Pageable pageable);

    @Query("""
            select c from Comment c join fetch c.user
            where c.post.id = :postId and c.deletedAt is null
              and (c.createdAt > :createdAt or (c.createdAt = :createdAt and c.id > :afterId))
            order by c.createdAt asc, c.id asc
            """)
    Slice<Comment> findActiveAfter(@Param("postId") String postId,
            @Param("createdAt") OffsetDateTime createdAt, @Param("afterId") String afterId, Pageable pageable);

    /** Revalidate cached visible comments in bounded batches, restricted to one post. */
    @Query("""
            select c from Comment c join fetch c.user
            where c.post.id = :postId and c.id in :ids and c.deletedAt is null
            order by c.createdAt asc, c.id asc
            """)
    List<Comment> findActiveByPostIdAndIds(@Param("postId") String postId, @Param("ids") List<String> ids);

    // Include soft-deleted markers: deleting a displayed comment must not invalidate its cursor.
    Optional<Comment> findByIdAndPostId(String id, String postId);

    @Query("select c from Comment c join fetch c.user where c.id = :id and c.deletedAt is null")
    Optional<Comment> findActiveById(@Param("id") String id);

    /** Find the parent without loading a stale comment before acquiring its lock. */
    @Query("select c.post.id from Comment c where c.id = :id and c.deletedAt is null")
    Optional<String> findActivePostIdByCommentId(@Param("id") String id);

    /** comment_count가 틀어졌을 때 쓰는 실제 집계값. */
    long countByPostIdAndDeletedAtIsNull(String postId);
}
