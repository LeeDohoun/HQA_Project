package com.hqa.backend.repository;

import com.hqa.backend.entity.UserSecret;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UserSecretRepository extends JpaRepository<UserSecret, String> {
}
