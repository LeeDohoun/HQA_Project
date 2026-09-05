package com.hqa.backend.repository;

import com.hqa.backend.entity.User;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Lock;
import jakarta.persistence.LockModeType;

public interface UserRepository extends JpaRepository<User, String> {
    boolean existsByUserId(String userId);

    Optional<User> findByUserId(String userId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT u FROM User u WHERE u.userId = :userId")
    Optional<User> lockByUserId(String userId);

    @Query("SELECT u FROM User u JOIN FETCH u.secret JOIN FETCH u.preference WHERE u.active = true")
    List<User> findAllActiveWithSecretAndPreference();
}
