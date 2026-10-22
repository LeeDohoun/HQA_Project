package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.hqa.backend.dto.*;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.enums.InquiryStatus;
import com.hqa.backend.entity.enums.UserRole;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.UserRepository;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;
import java.util.function.IntFunction;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ContextConfiguration;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

/** Real PostgreSQL checks; use a disposable database named hqa_board_integrity. */
@DataJpaTest(showSql = false, properties = {
        "spring.jpa.hibernate.ddl-auto=validate", "spring.flyway.baseline-on-migrate=false",
        "logging.level.org.springframework.jdbc=WARN", "logging.level.org.hibernate=WARN", "logging.level.org.hibernate.SQL=WARN" })
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@ContextConfiguration(classes = CommunityPostgresTest.PersistenceConfig.class)
@Transactional(propagation = Propagation.NOT_SUPPORTED)
@EnabledIfEnvironmentVariable(named = "HQA_TEST_DATABASE_URL", matches = "jdbc:postgresql:.*")
class CommunityPostgresTest {
    @Configuration(proxyBeanMethods = false)
    @EntityScan("com.hqa.backend.entity")
    @EnableJpaRepositories("com.hqa.backend.repository")
    @Import({CommunityService.class, CommunityPointsService.class, com.hqa.backend.repository.CommunityPointsRepository.class})
    static class PersistenceConfig { }

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry registry) {
        String url = System.getenv("HQA_TEST_DATABASE_URL");
        if (url == null || !url.endsWith("/hqa_board_integrity")) {
            throw new IllegalArgumentException("Only the disposable hqa_board_integrity database is allowed");
        }
        registry.add("spring.datasource.url", () -> url);
        registry.add("spring.datasource.username", () -> System.getenv("HQA_TEST_DATABASE_USERNAME"));
        registry.add("spring.datasource.password", () -> System.getenv("HQA_TEST_DATABASE_PASSWORD"));
    }

    @Autowired CommunityService service;
    @Autowired CommunityPointsService points;
    @Autowired UserRepository users;
    @Autowired JdbcTemplate jdbc;
    @Autowired PlatformTransactionManager transactions;
    private User owner;
    private User other;
    private User admin;

    @BeforeEach
    void resetDisposableData() {
        jdbc.update("delete from community_point_events");
        jdbc.update("delete from post_recommendations");
        jdbc.update("delete from comments");
        jdbc.update("delete from posts");
        jdbc.update("delete from users");
        owner = createUser("owner", UserRole.user);
        other = createUser("other", UserRole.user);
        admin = createUser("admin", UserRole.admin);
    }

    @Test
    void writingAndCommentsGiveSeparateBoardPoints() {
        var free = createPost();
        var stock = service.createStock(owner, new PostCreateRequest("stock", "body", "005930"));
        service.addComment(other, free.id(), new CommentRequest("free reply"));
        service.addComment(other, stock.id(), new CommentRequest("stock reply"));
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(10);
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.STOCK)).isEqualTo(10);
        assertThat(boardPoints(other, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(2);
        assertThat(boardPoints(other, com.hqa.backend.entity.enums.BoardType.STOCK)).isEqualTo(2);
    }

    @Test
    void boardsUseDifferentLevelThresholdsAndTitles() {
        for (int i = 0; i < 5; i++) {
            createPost();
            service.createStock(owner, new PostCreateRequest("stock", "body", "005930"));
        }
        var profile = points.profile(owner);
        assertThat(profile.totalPoints()).isEqualTo(100);
        assertThat(profile.boards().get(0).points()).isEqualTo(50);
        assertThat(profile.boards().get(0).level()).isEqualTo(2);
        assertThat(profile.boards().get(0).title()).isEqualTo("이야기꾼");
        assertThat(profile.boards().get(1).points()).isEqualTo(50);
        assertThat(profile.boards().get(1).level()).isEqualTo(1);
        assertThat(profile.boards().get(1).title()).isEqualTo("시장 관찰자");
        assertThat(profile.boards().get(1).pointsToNextLevel()).isEqualTo(50);
        assertThat(service.listFree(0, 20).items().get(0).authorLevel()).isEqualTo(2);
    }

    @Test
    void inquiryWritingAndCommentsHaveNoPointsAndCannotBeRecommended() {
        var inquiry = service.createInquiry(owner, new InquiryCreateRequest("question", "body", null));
        service.addComment(owner, inquiry.id(), new CommentRequest("follow up"));
        service.addComment(admin, inquiry.id(), new CommentRequest("answer"));
        assertThat(points.profile(owner).totalPoints()).isZero();
        assertThat(points.profile(admin).totalPoints()).isZero();
        assertThat(inquiry.recommendable()).isFalse();
        assertThatThrownBy(() -> service.recommend(admin, inquiry.id()))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(400));
    }

    @Test
    void recommendationCreditsOnlyTheAuthorAndDuplicateOrSelfRecommendationFails() {
        var post = createPost();
        assertThatThrownBy(() -> service.recommend(owner, post.id()))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(400));
        var recommended = service.recommend(other, post.id());
        assertThat(recommended.recommendationCount()).isEqualTo(1);
        assertThat(recommended.recommended()).isTrue();
        assertThat(recommended.recommendable()).isFalse();
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(15);
        assertThat(points.profile(other).totalPoints()).isZero();
        assertThatThrownBy(() -> service.recommend(other, post.id()))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(409));
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(15);
        assertThatThrownBy(() -> jdbc.update("insert into post_recommendations(id,post_id,user_id) values ('duplicate',?,?)", post.id(), other.getId()))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void concurrentSameUserRecommendationsGiveExactlyOneAward() throws Exception {
        var post = createPost();
        var results = parallel(8, index -> {
            try { service.recommend(other, post.id()); return 201; }
            catch (ApiException error) { return error.getStatus(); }
        });
        assertThat(results.stream().filter(code -> code == 201)).hasSize(1);
        assertThat(results.stream().filter(code -> code == 409)).hasSize(7);
        assertThat(service.get(other, post.id()).recommendationCount()).isEqualTo(1);
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(15);
    }

    @Test
    void simultaneousContributionsOnDifferentPostsDoNotLosePoints() throws Exception {
        var posts = parallel(8, index -> createPost());
        parallel(8, index -> service.recommend(other, posts.get(index).id()));
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(120);
        assertThat(points.profile(owner).boards().get(0).recommendationsReceived()).isEqualTo(8);
    }

    @Test
    void failedTransactionRollsBackRecommendationCountRecordAndPointsTogether() {
        var post = createPost();
        assertThatThrownBy(() -> new TransactionTemplate(transactions).execute(status -> {
            service.recommend(other, post.id());
            throw new IllegalStateException("rollback");
        })).isInstanceOf(IllegalStateException.class);
        assertThat(service.get(other, post.id()).recommendationCount()).isZero();
        assertThat(service.get(other, post.id()).recommended()).isFalse();
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(10);
        assertThat(jdbc.queryForObject("select count(*) from post_recommendations", Long.class)).isZero();
    }

    @Test
    void deletionReversesRelatedAwardsWithoutRemovingPermanentRecommendations() {
        var post = createPost();
        var comment = service.addComment(other, post.id(), new CommentRequest("reply"));
        service.recommend(other, post.id());
        service.createStock(owner, new PostCreateRequest("stock", "body", "005930"));
        service.delete(owner, post.id());
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isZero();
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.STOCK)).isEqualTo(10);
        assertThat(points.profile(other).totalPoints()).isZero();
        assertThat(jdbc.queryForObject("select count(*) from post_recommendations where post_id=?", Long.class, post.id())).isEqualTo(1);
        service.deleteComment(other, comment.id());
        assertThat(points.profile(other).totalPoints()).isZero();
        assertThatThrownBy(() -> service.recommend(admin, post.id()))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(404));
    }

    @Test
    void commentDeletionCannotBeUsedToFarmPoints() {
        var post = createPost();
        for (int i = 0; i < 3; i++) {
            var comment = service.addComment(other, post.id(), new CommentRequest("reply"));
            assertThat(points.profile(other).totalPoints()).isEqualTo(2);
            service.deleteComment(other, comment.id());
            assertThat(points.profile(other).totalPoints()).isZero();
        }
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(10);
    }

    @Test
    void duplicateAwardAndInvalidLedgerAmountsAreRejectedByDatabase() {
        var post = createPost();
        assertThatThrownBy(() -> jdbc.update("""
            insert into community_point_events(id,user_id,board_type,reason,points,post_id)
            values ('duplicate',?,'FREE','POST_CREATED',10,?)
            """, owner.getId(), post.id())).isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(() -> jdbc.update("""
            insert into community_point_events(id,user_id,board_type,reason,points,post_id)
            values ('invalid',?,'FREE','POST_CREATED',-10,?)
            """, owner.getId(), post.id())).isInstanceOf(DataIntegrityViolationException.class);
        assertThat(boardPoints(owner, com.hqa.backend.entity.enums.BoardType.FREE)).isEqualTo(10);
    }

    @Test
    void nicknameUpdateIsUniqueAndDoesNotChangePoints() {
        createPost();
        var updated = points.updateNickname(owner, new NicknameRequest(" 게시판닉네임 "));
        assertThat(updated.nickname()).isEqualTo("게시판닉네임");
        assertThat(updated.totalPoints()).isEqualTo(10);
        assertThatThrownBy(() -> points.updateNickname(other, new NicknameRequest("게시판닉네임")))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getStatus()).isEqualTo(409));
        assertThat(users.findById(other.getId()).orElseThrow().getNickname()).isEqualTo("other");
        assertThat(service.listFree(0, 20).items().get(0).authorNickname()).isEqualTo("게시판닉네임");
    }

    private long boardPoints(User user, com.hqa.backend.entity.enums.BoardType board) {
        return points.profile(user).boards().stream().filter(item -> item.boardType() == board).findFirst().orElseThrow().points();
    }

    @Test
    void databaseRejectsMissingInquiryStatus() {
        var inquiry = service.createInquiry(owner, new InquiryCreateRequest("question", "body", null));
        assertThatThrownBy(() -> jdbc.update("update posts set inquiry_status=null where id=?", inquiry.id()))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void databaseRejectsBlankStockCodesAndMismatchedDeletionFields() {
        var stock = service.createStock(owner, new PostCreateRequest("stock", "body", "005930"));
        assertThatThrownBy(() -> jdbc.update("update posts set stock_code='   ' where id=?", stock.id()))
                .isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(() -> jdbc.update("update posts set deleted_at=now() where id=?", stock.id()))
                .isInstanceOf(DataIntegrityViolationException.class);
        var comment = service.addComment(owner, stock.id(), new CommentRequest("comment"));
        assertThatThrownBy(() -> jdbc.update("update comments set deleted_by=? where id=?", owner.getId(), comment.id()))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void simultaneousCommentCreationKeepsExactCount() throws Exception {
        var post = createPost();
        var results = parallel(24, index -> service.addComment(owner, post.id(), new CommentRequest("comment " + index)).id());
        assertThat(results).doesNotHaveDuplicates().hasSize(24);
        assertCounts(post.id(), 24);
    }

    @Test
    void simultaneousDuplicateDeletionDecrementsOnlyOnce() throws Exception {
        var post = createPost();
        var comment = service.addComment(owner, post.id(), new CommentRequest("comment"));
        var results = parallel(12, index -> {
            try { service.deleteComment(owner, comment.id()); return 204; }
            catch (ApiException error) { return error.getStatus(); }
        });
        assertThat(results).containsOnly(204, 404);
        assertThat(results.stream().filter(status -> status == 204).count()).isEqualTo(1);
        assertCounts(post.id(), 0);
    }

    @Test
    void deletionWaitingForThirdCommentRechecksCount() throws Exception {
        var post = createPost();
        service.addComment(owner, post.id(), new CommentRequest("one"));
        service.addComment(owner, post.id(), new CommentRequest("two"));
        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<Integer> deletion = new TransactionTemplate(transactions).execute(status -> {
                lockPost(post.id());
                Future<Integer> waiting = executor.submit(() -> {
                    try { service.delete(owner, post.id()); return 204; }
                    catch (ApiException error) { return error.getStatus(); }
                });
                awaitBlockedPostMutation();
                service.addComment(owner, post.id(), new CommentRequest("three"));
                return waiting;
            });
            assertThat(deletion.get(15, TimeUnit.SECONDS)).isEqualTo(409);
            assertCounts(post.id(), 3);
            assertThat(service.get(owner, post.id()).id()).isEqualTo(post.id());
        } finally { stop(executor); }
    }

    @Test
    void commentWaitingForPostDeletionCannotCreateOrphanedVisibleComment() throws Exception {
        var post = createPost();
        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<Integer> addition = new TransactionTemplate(transactions).execute(status -> {
                lockPost(post.id());
                Future<Integer> waiting = executor.submit(() -> {
                    try { service.addComment(owner, post.id(), new CommentRequest("late")); return 201; }
                    catch (ApiException error) { return error.getStatus(); }
                });
                awaitBlockedPostMutation();
                service.delete(owner, post.id());
                return waiting;
            });
            assertThat(addition.get(15, TimeUnit.SECONDS)).isEqualTo(404);
            assertCounts(post.id(), 0);
        } finally { stop(executor); }
    }

    @Test
    void equalTimestampCursorHasNoDuplicatesOrGapsEvenAfterMarkerDeletion() {
        var post = createPost();
        for (int index = 0; index < 45; index++) service.addComment(owner, post.id(), new CommentRequest("comment " + index));
        jdbc.update("update comments set created_at='2026-10-10T10:00:00.123456Z' where post_id=?", post.id());
        var initial = service.get(owner, post.id());
        assertThat(initial.comments()).hasSize(20);
        assertThat(initial.hasMoreComments()).isTrue();
        String deletedMarker = initial.nextCommentCursor();
        service.deleteComment(owner, deletedMarker);
        List<String> seen = new ArrayList<>(initial.comments().stream().map(CommentResponse::id)
                .filter(id -> !id.equals(deletedMarker)).toList());
        String cursor = deletedMarker;
        boolean more = true;
        while (more) {
            var page = service.listComments(owner, post.id(), cursor, 20);
            assertThat(page.items().size()).isLessThanOrEqualTo(20);
            assertThat(page.totalItems()).isEqualTo(44);
            seen.addAll(page.items().stream().map(CommentResponse::id).toList());
            cursor = page.nextCursor();
            more = page.hasMore();
        }
        var expected = jdbc.queryForList("select id from comments where post_id=? and deleted_at is null order by created_at,id", String.class, post.id());
        assertThat(seen).containsExactlyElementsOf(expected).doesNotHaveDuplicates();
        assertCounts(post.id(), 44);
    }

    @Test
    void privateInquiriesAndTheirCommentsStayOutOfPublicQueries() {
        var inquiry = service.createInquiry(owner, new InquiryCreateRequest("private", "body", null));
        service.addComment(owner, inquiry.id(), new CommentRequest("private reply"));
        var publicPost = createPost();
        var stock = service.createStock(owner, new PostCreateRequest("stock", "body", "005930"));
        assertThat(service.listFree(0, 20).items()).extracting(PostSummary::id).containsExactly(publicPost.id());
        assertThat(service.listStock(null, 0, 20).items()).extracting(PostSummary::id).containsExactly(stock.id());
        assertThat(service.listStock("000660", 0, 20).items()).isEmpty();
        assertThat(service.listInquiries(other, 0, 20).items()).isEmpty();
        assertThat(service.listInquiries(admin, 0, 20).items()).extracting(PostSummary::id).containsExactly(inquiry.id());
        assertThatThrownBy(() -> service.get(other, inquiry.id())).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getStatus()).isEqualTo(403));
        assertThatThrownBy(() -> service.listComments(other, inquiry.id(), null, 20)).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getStatus()).isEqualTo(403));
    }

    @Test
    void adminResolutionCommitsTargetDeletionAndInquiryTogether() {
        var post = createPost();
        var inquiry = service.createInquiry(owner, new InquiryCreateRequest("delete request", "body", post.id()));
        var resolved = service.resolveInquiry(admin, inquiry.id(), new InquiryResolveRequest(InquiryStatus.RESOLVED, "done", true));
        assertThat(resolved.inquiryStatus()).isEqualTo(InquiryStatus.RESOLVED);
        assertThat(resolved.targetPostDeletable()).isFalse();
        assertThatThrownBy(() -> service.get(owner, post.id())).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getStatus()).isEqualTo(404));
        assertThat(jdbc.queryForObject("select deleted_by from posts where id=?", String.class, post.id())).isEqualTo(admin.getId());
    }

    @Test
    void failedResolutionLeavesInquiryOpen() {
        var post = createPost();
        var inquiry = service.createInquiry(owner, new InquiryCreateRequest("delete request", "body", post.id()));
        service.delete(admin, post.id());
        assertThatThrownBy(() -> service.resolveInquiry(admin, inquiry.id(), new InquiryResolveRequest(InquiryStatus.RESOLVED, "done", true)))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getStatus()).isEqualTo(400));
        var unchanged = service.get(owner, inquiry.id());
        assertThat(unchanged.inquiryStatus()).isEqualTo(InquiryStatus.OPEN);
        assertThat(unchanged.adminReply()).isNull();
    }

    @Test
    void postPaginationUsesDeterministicIdOrderForEqualTimestamps() {
        for (int index = 0; index < 6; index++) createPost();
        jdbc.update("update posts set created_at='2026-10-10T10:00:00Z'");
        var expected = jdbc.queryForList("select id from posts order by created_at desc,id desc", String.class);
        List<String> seen = new ArrayList<>();
        for (int page = 0; page < 3; page++) seen.addAll(service.listFree(page, 2).items().stream().map(PostSummary::id).toList());
        assertThat(seen).containsExactlyElementsOf(expected).doesNotHaveDuplicates();
    }

    @Test
    void visibleCommentChecksCannotReturnDeletedForeignOrPrivateComments() {
        var post = createPost();
        var kept = service.addComment(owner, post.id(), new CommentRequest("kept"));
        var deleted = service.addComment(owner, post.id(), new CommentRequest("deleted"));
        var otherPost = createPost();
        var foreign = service.addComment(owner, otherPost.id(), new CommentRequest("foreign"));
        service.deleteComment(owner, deleted.id());
        var result = service.visibleComments(owner, post.id(), List.of(kept.id(), kept.id(), deleted.id(), foreign.id(), "unknown"));
        assertThat(result.items()).extracting(CommentResponse::id).containsExactly(kept.id());
        assertThat(result.totalItems()).isEqualTo(1);
        assertThat(result.hasMore()).isFalse();
        assertThatThrownBy(() -> service.visibleComments(owner, post.id(), java.util.Collections.nCopies(51, kept.id())))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getStatus()).isEqualTo(400));
        var inquiry = service.createInquiry(owner, new InquiryCreateRequest("private", "body", null));
        assertThatThrownBy(() -> service.visibleComments(other, inquiry.id(), List.of(kept.id())))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getStatus()).isEqualTo(403));
    }

    @Test
    void writeResponsesExposeTheSavedUpdateTime() {
        var post = createPost();
        var beforeEdit = service.get(owner, post.id()).updatedAt();
        var edited = service.update(owner, post.id(), new PostUpdateRequest("changed title", "changed body"));
        assertThat(edited.updatedAt()).isAfter(beforeEdit);
        assertSavedTime(post.id(), edited);
        var inquiry = service.createInquiry(owner, new InquiryCreateRequest("question", "body", null));
        var beforeResolve = service.get(owner, inquiry.id()).updatedAt();
        var resolved = service.resolveInquiry(admin, inquiry.id(), new InquiryResolveRequest(InquiryStatus.RESOLVED, "answer", false));
        assertThat(resolved.updatedAt()).isAfter(beforeResolve);
        assertSavedTime(inquiry.id(), resolved);
    }

    private void assertSavedTime(String id, PostResponse response) {
        var stored = jdbc.queryForObject("select updated_at from posts where id=?", java.sql.Timestamp.class, id).toInstant();
        assertThat(response.updatedAt().toInstant()).isEqualTo(stored);
    }

    private User createUser(String name, UserRole role) {
        User user = new User();
        user.setUserId(name); user.setFirstName(name); user.setLastName("test"); user.setPassword("test-only"); user.setRole(role);
        return users.saveAndFlush(user);
    }
    private PostResponse createPost() { return service.createFree(owner, new PostCreateRequest("post", "body", null)); }
    private void assertCounts(String postId, int count) {
        assertThat(jdbc.queryForObject("select comment_count from posts where id=?", Integer.class, postId)).isEqualTo(count);
        assertThat(jdbc.queryForObject("select count(*) from comments where post_id=? and deleted_at is null", Integer.class, postId)).isEqualTo(count);
    }
    private void lockPost(String postId) { jdbc.queryForObject("select id from posts where id=? for update", String.class, postId); }
    private void awaitBlockedPostMutation() {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(8);
        while (System.nanoTime() < deadline) {
            jdbc.execute("select pg_stat_clear_snapshot()");
            Integer waiting = jdbc.queryForObject("select count(*) from pg_stat_activity where datname=current_database() and pid<>pg_backend_pid() and wait_event_type='Lock' and query ilike '%posts%'", Integer.class);
            if (waiting != null && waiting > 0) return;
            try { Thread.sleep(25); } catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new AssertionError(error); }
        }
        throw new AssertionError("Competing mutation did not wait on the parent row lock");
    }
    private <T> List<T> parallel(int count, IntFunction<T> work) throws Exception {
        ExecutorService executor = Executors.newFixedThreadPool(count);
        CountDownLatch ready = new CountDownLatch(count);
        CountDownLatch start = new CountDownLatch(1);
        try {
            List<Future<T>> futures = new ArrayList<>();
            for (int index = 0; index < count; index++) {
                int taskIndex = index;
                futures.add(executor.submit(() -> { ready.countDown(); if (!start.await(10, TimeUnit.SECONDS)) throw new AssertionError("Start timeout"); return work.apply(taskIndex); }));
            }
            assertThat(ready.await(10, TimeUnit.SECONDS)).isTrue();
            start.countDown();
            List<T> results = new ArrayList<>();
            for (Future<T> future : futures) results.add(future.get(20, TimeUnit.SECONDS));
            return results;
        } finally { start.countDown(); stop(executor); }
    }
    private void stop(ExecutorService executor) throws InterruptedException {
        executor.shutdownNow();
        assertThat(executor.awaitTermination(10, TimeUnit.SECONDS)).isTrue();
    }
}
