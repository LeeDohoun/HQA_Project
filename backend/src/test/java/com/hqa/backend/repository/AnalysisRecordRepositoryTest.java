package com.hqa.backend.repository;

import static org.assertj.core.api.Assertions.assertThat;

import com.hqa.backend.entity.AnalysisRecord;
import com.hqa.backend.entity.User;
import jakarta.persistence.EntityManager;
import java.time.OffsetDateTime;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

/** Opt-in PostgreSQL checks for the conditional writes used by concurrent result polls. */
@DataJpaTest(properties = "spring.jpa.hibernate.ddl-auto=validate")
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@EnabledIfEnvironmentVariable(named = "HQA_TEST_DATABASE_URL", matches = "jdbc:postgresql:.*")
class AnalysisRecordRepositoryTest {
    @Autowired AnalysisRecordRepository records;
    @Autowired UserRepository users;
    @Autowired EntityManager entityManager;

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry properties) {
        properties.add("spring.datasource.url", () -> System.getenv("HQA_TEST_DATABASE_URL"));
        properties.add("spring.datasource.username", () -> System.getenv("HQA_TEST_DATABASE_USERNAME"));
        properties.add("spring.datasource.password", () -> System.getenv("HQA_TEST_DATABASE_PASSWORD"));
    }

    @Test
    void latePollsCannotReplaceOrRegressAStoredTerminalResult() {
        AnalysisRecord record = newRecord();
        String taskId = record.getTaskId(), owner = record.getUser().getId();
        OffsetDateTime finished = record.getCreatedAt().plusSeconds(2).truncatedTo(java.time.temporal.ChronoUnit.MICROS);
        assertThat(records.markRunning(taskId, owner)).isEqualTo(1);
        assertThat(records.storeResultIfAbsent(taskId, owner, "completed", "삼성전자", finished, "first-result")).isEqualTo(1);
        assertThat(records.markRunning(taskId, owner)).isZero();
        assertThat(records.storeResultIfAbsent(taskId, owner, "failed", "삼성전자", finished, "late-result")).isZero();
        entityManager.clear();
        AnalysisRecord stored = records.findByTaskIdAndUser_Id(taskId, owner).orElseThrow();
        assertThat(stored.getStatus()).isEqualTo("completed");
        assertThat(stored.getResultJson()).isEqualTo("first-result");
        assertThat(stored.getCompletedAt().toInstant()).isEqualTo(finished.toInstant());
    }

    @Test
    void allReadsAndConditionalWritesRequireTheOwner() {
        AnalysisRecord record = newRecord();
        assertThat(records.findByTaskIdAndUser_Id(record.getTaskId(), "another-user")).isEmpty();
        assertThat(records.markRunning(record.getTaskId(), "another-user")).isZero();
        assertThat(records.storeResultIfAbsent(record.getTaskId(), "another-user", "failed", "other",
                OffsetDateTime.now(), "unauthorized-result")).isZero();
        assertThat(records.findByUser_IdOrderByCreatedAtDesc("another-user",
                org.springframework.data.domain.PageRequest.of(0, 20))).isEmpty();
    }

    private AnalysisRecord newRecord() {
        User user = new User();
        user.setUserId("review-" + UUID.randomUUID());
        user.setFirstName("테스트"); user.setLastName("사용자"); user.setPassword("test-only");
        users.saveAndFlush(user);
        AnalysisRecord record = new AnalysisRecord();
        record.setUser(user); record.setTaskId(UUID.randomUUID().toString());
        record.setStockCode("005930"); record.setStockName("삼성전자"); record.setMaxRetries(0);
        records.saveAndFlush(record);
        entityManager.clear();
        return record;
    }
}
