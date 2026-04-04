package com.hqa.backend.service;

import com.hqa.backend.dto.AuthLoginRequest;
import com.hqa.backend.dto.AuthResponse;
import com.hqa.backend.dto.AuthSignupRequest;
import com.hqa.backend.dto.AuthUserResponse;
import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.UserSecretRequest;
import com.hqa.backend.dto.UserSecretResponse;
import com.hqa.backend.dto.UserSurveyRequest;
import com.hqa.backend.dto.UserSurveyResponse;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.entity.UserSurvey;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.UserRepository;
import com.hqa.backend.repository.UserSecretRepository;
import com.hqa.backend.repository.UserSurveyRepository;
import jakarta.servlet.http.HttpSession;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional
public class AuthService {

    public static final String SESSION_USER_ID = "AUTH_USER_ID";

    private final UserRepository userRepository;
    private final UserSecretRepository userSecretRepository;
    private final UserSurveyRepository userSurveyRepository;
    private final PasswordEncoder passwordEncoder;

    public AuthService(UserRepository userRepository,
                       UserSecretRepository userSecretRepository,
                       UserSurveyRepository userSurveyRepository,
                       PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.userSecretRepository = userSecretRepository;
        this.userSurveyRepository = userSurveyRepository;
        this.passwordEncoder = passwordEncoder;
    }

    public AuthResponse signup(AuthSignupRequest request, HttpSession session) {
        if (userRepository.existsByUserId(request.userId())) {
            throw new ApiException(ErrorCode.USER_ALREADY_EXISTS, 409, "User ID already exists", request.userId());
        }

        User user = new User();
        user.setUserId(request.userId().trim());
        user.setFirstName(request.firstName().trim());
        user.setLastName(request.lastName().trim());
        user.setPassword(passwordEncoder.encode(request.password()));

        User savedUser = userRepository.save(user);
        session.setAttribute(SESSION_USER_ID, savedUser.getId());
        return new AuthResponse(true, "Sign up completed", toUserResponse(savedUser));
    }

    @Transactional(readOnly = true)
    public AuthResponse login(AuthLoginRequest request, HttpSession session) {
        User user = userRepository.findByUserId(request.userId().trim())
                .orElseThrow(() -> new ApiException(ErrorCode.INVALID_CREDENTIALS, 401, "Invalid user ID or password", null));

        if (!user.isActive()) {
            throw new ApiException(ErrorCode.USER_INACTIVE, 403, "Inactive user", user.getUserId());
        }
        if (!passwordEncoder.matches(request.password(), user.getPassword())) {
            throw new ApiException(ErrorCode.INVALID_CREDENTIALS, 401, "Invalid user ID or password", null);
        }

        session.setAttribute(SESSION_USER_ID, user.getId());
        return new AuthResponse(true, "Login completed", toUserResponse(user));
    }

    public AuthResponse logout(HttpSession session) {
        session.invalidate();
        return new AuthResponse(true, "Logout completed", null);
    }

    @Transactional(readOnly = true)
    public AuthUserResponse getCurrentUser(HttpSession session) {
        return toUserResponse(requireUser(session));
    }

    public UserSecretResponse upsertUserSecret(UserSecretRequest request, HttpSession session) {
        User user = requireUser(session);
        UserSecret secret = user.getSecret();
        if (secret == null) {
            secret = new UserSecret();
            secret.setUser(user);
            user.setSecret(secret);
        }

        secret.setKisAppKey(request.kisAppKey().trim());
        secret.setKisAppSecret(request.kisAppSecret().trim());
        secret.setKisAccountNo(request.kisAccountNo().trim());

        userSecretRepository.save(secret);
        return toSecretResponse(secret);
    }

    public UserSurveyResponse saveSurvey(UserSurveyRequest request, HttpSession session) {
        User user = requireUser(session);
        UserSurvey survey = user.getSurvey();
        if (survey == null) {
            survey = new UserSurvey();
            survey.setUser(user);
            user.setSurvey(survey);
        }

        survey.setInvestmentExperience(request.investmentExperience().trim());
        survey.setRiskTolerance(request.riskTolerance().trim());
        survey.setInvestmentGoal(request.investmentGoal().trim());
        survey.setPreferredMarket(request.preferredMarket().trim());
        survey.setNotes(request.notes() == null ? null : request.notes().trim());

        UserSurvey savedSurvey = userSurveyRepository.save(survey);
        return toSurveyResponse(savedSurvey);
    }

    @Transactional(readOnly = true)
    public UserSurveyResponse getSurvey(HttpSession session) {
        User user = requireUser(session);
        UserSurvey survey = user.getSurvey();
        if (survey == null) {
            throw new ApiException(ErrorCode.SURVEY_NOT_FOUND, 404, "Survey not found", user.getUserId());
        }
        return toSurveyResponse(survey);
    }

    @Transactional(readOnly = true)
    public UserSecretResponse getUserSecret(HttpSession session) {
        User user = requireUser(session);
        return toSecretResponse(user.getSecret());
    }

    @Transactional(readOnly = true)
    public User requireUser(HttpSession session) {
        Object userId = session.getAttribute(SESSION_USER_ID);
        if (!(userId instanceof String userPk) || userPk.isBlank()) {
            throw new ApiException(ErrorCode.UNAUTHORIZED, 401, "Login required", null);
        }
        return userRepository.findById(userPk)
                .orElseThrow(() -> new ApiException(ErrorCode.UNAUTHORIZED, 401, "Invalid session", null));
    }

    @Transactional(readOnly = true)
    public UserSecret requireUserSecret(HttpSession session) {
        UserSecret secret = requireUser(session).getSecret();
        if (secret == null
                || isBlank(secret.getKisAppKey())
                || isBlank(secret.getKisAppSecret())
                || isBlank(secret.getKisAccountNo())) {
            throw new ApiException(ErrorCode.KIS_SECRET_NOT_CONFIGURED, 400, "KIS credentials are not configured", null);
        }
        return secret;
    }

    private AuthUserResponse toUserResponse(User user) {
        return new AuthUserResponse(
                user.getId(),
                user.getUserId(),
                user.getFirstName(),
                user.getLastName(),
                user.getRole(),
                user.isActive(),
                user.getSecret() != null && !isBlank(user.getSecret().getKisAppKey()),
                user.getSurvey() != null,
                user.getCreatedAt()
        );
    }

    private UserSecretResponse toSecretResponse(UserSecret secret) {
        if (secret == null) {
            return new UserSecretResponse(false, null, null);
        }
        return new UserSecretResponse(
                !isBlank(secret.getKisAppKey()) && !isBlank(secret.getKisAppSecret()) && !isBlank(secret.getKisAccountNo()),
                mask(secret.getKisAppKey(), 4),
                mask(secret.getKisAccountNo(), 4)
        );
    }

    private UserSurveyResponse toSurveyResponse(UserSurvey survey) {
        return new UserSurveyResponse(
                survey.getInvestmentExperience(),
                survey.getRiskTolerance(),
                survey.getInvestmentGoal(),
                survey.getPreferredMarket(),
                survey.getNotes(),
                survey.getUpdatedAt()
        );
    }

    private String mask(String value, int visibleSuffix) {
        if (isBlank(value)) {
            return null;
        }
        int maskedLength = Math.max(0, value.length() - visibleSuffix);
        return "*".repeat(maskedLength) + value.substring(Math.max(0, value.length() - visibleSuffix));
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
