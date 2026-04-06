package com.hqa.backend.repository;

import com.hqa.backend.entity.UserSurvey;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UserSurveyRepository extends JpaRepository<UserSurvey, String> {
}
