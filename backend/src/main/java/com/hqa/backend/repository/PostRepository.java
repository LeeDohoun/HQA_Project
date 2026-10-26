package com.hqa.backend.repository;

import com.hqa.backend.dto.PostSummary;
import com.hqa.backend.entity.Post;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * 공개 게시판(FREE/STOCK)과 문의(INQUIRY)가 같은 테이블을 쓴다.
 * board_type 조건이 없는 범용 목록 메서드는 일부러 두지 않는다 —
 * 그런 메서드가 있으면 언젠가 문의글이 공개 목록에 섞인다.
 */
public interface PostRepository extends JpaRepository<Post, String> {

    String SUMMARY = """
            select new com.hqa.backend.dto.PostSummary(
                p.id, p.boardType, p.title, p.stockCode, p.commentCount, p.inquiryStatus,
                u.id, u.userId, u.firstName, u.lastName, p.createdAt, p.updatedAt, u.nickname, p.recommendationCount)
            from Post p join p.user u
            """;

    /** 자유게시판 목록. INQUIRY는 구조적으로 걸릴 수 없다. */
    @Query(SUMMARY + " where p.boardType = com.hqa.backend.entity.enums.BoardType.FREE and p.deletedAt is null order by p.createdAt desc, p.id desc")
    Page<PostSummary> findFreeBoard(Pageable pageable);

    /** 종목토론방 목록. stockCode가 null이면 전체 종목. */
    @Query(SUMMARY + """
             where p.boardType = com.hqa.backend.entity.enums.BoardType.STOCK
               and p.deletedAt is null
               and (:stockCode is null or p.stockCode = :stockCode)
             order by p.createdAt desc, p.id desc
            """)
    Page<PostSummary> findStockBoard(@Param("stockCode") String stockCode, Pageable pageable);

    /** 내 문의 목록. 작성자 본인만 본다. */
    @Query(SUMMARY + """
             where p.boardType = com.hqa.backend.entity.enums.BoardType.INQUIRY
               and p.deletedAt is null
               and p.user.id = :userId
             order by p.createdAt desc, p.id desc
            """)
    Page<PostSummary> findMyInquiries(@Param("userId") String userId, Pageable pageable);

    /** 관리자용 문의 목록. 호출 전에 반드시 관리자 확인을 거쳐야 한다. */
    @Query(SUMMARY + """
             where p.boardType = com.hqa.backend.entity.enums.BoardType.INQUIRY
               and p.deletedAt is null
             order by p.createdAt desc, p.id desc
            """)
    Page<PostSummary> findAllInquiriesForAdmin(Pageable pageable);

    /** 상세 조회. 작성자를 함께 가져와 N+1을 막는다. */
    @Query("select p from Post p join fetch p.user where p.id = :id and p.deletedAt is null")
    Optional<Post> findActiveById(@Param("id") String id);

    /** Serialize all post mutations on the parent row. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select p from Post p where p.id = :id")
    Optional<Post> findByIdForUpdate(@Param("id") String id);
}
