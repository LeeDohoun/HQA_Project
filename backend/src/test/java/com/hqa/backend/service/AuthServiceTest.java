package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.dto.AuthSignupRequest;
import com.hqa.backend.dto.AuthLoginRequest;
import org.springframework.dao.DataIntegrityViolationException;
import java.sql.SQLException;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.enums.UserRole;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.*;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.crypto.password.PasswordEncoder;

class AuthServiceTest {
    private final UserRepository users = mock(UserRepository.class);
    private final PasswordEncoder passwords = mock(PasswordEncoder.class);
    private final AuthService service = new AuthService(users, mock(UserSecretRepository.class),
            mock(UserPreferenceRepository.class), passwords, mock(SecretCipher.class), mock(KisClient.class));

    @Test
    void activeSessionStillWorks() {
        User user = user(UserRole.user);
        when(users.findById(user.getId())).thenReturn(Optional.of(user));
        assertThat(service.requireUser(session(user))).isSameAs(user);
    }

    @Test
    void deactivationRevokesExistingSession() {
        User user = user(UserRole.user);
        MockHttpSession session = session(user);
        when(users.findById(user.getId())).thenReturn(Optional.of(user));
        assertThat(service.requireUser(session)).isSameAs(user);
        user.setActive(false);
        assertThatThrownBy(() -> service.requireUser(session)).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getErrorCode()).isEqualTo(ErrorCode.USER_INACTIVE));
        assertThat(session.getAttribute(AuthService.SESSION_USER_ID)).isNull();
        assertThatThrownBy(() -> service.requireUser(session)).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getStatus()).isEqualTo(401));
    }

    @Test
    void inactiveAdminCannotUseAdminApis() {
        User admin = user(UserRole.admin);
        admin.setActive(false);
        when(users.findById(admin.getId())).thenReturn(Optional.of(admin));
        assertThatThrownBy(() -> service.requireAdmin(session(admin))).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getErrorCode()).isEqualTo(ErrorCode.USER_INACTIVE));
    }

    @Test
    void roleDemotionTakesEffectForExistingSession() {
        User admin = user(UserRole.admin);
        MockHttpSession session = session(admin);
        when(users.findById(admin.getId())).thenReturn(Optional.of(admin));
        assertThat(service.requireAdmin(session)).isSameAs(admin);
        admin.setRole(UserRole.user);
        assertThatThrownBy(() -> service.requireAdmin(session)).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getStatus()).isEqualTo(403));
    }

    @Test
    void missingAndDeletedAccountsCannotAuthenticate() {
        assertThatThrownBy(() -> service.requireUser(new MockHttpSession())).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getStatus()).isEqualTo(401));
        verifyNoInteractions(users);
        User user = user(UserRole.user);
        when(users.findById(user.getId())).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.requireUser(session(user))).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getStatus()).isEqualTo(401));
    }

    @Test
    void wrongPasswordDoesNotRevealInactiveAccountStatus() {
        User user = user(UserRole.user); user.setPassword("hash"); user.setActive(false);
        when(users.findByUserId("test")).thenReturn(Optional.of(user));
        when(passwords.matches("wrong", "hash")).thenReturn(false);
        assertThatThrownBy(() -> service.login(new AuthLoginRequest("test", "wrong"), new MockHttpSession()))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getErrorCode()).isEqualTo(ErrorCode.INVALID_CREDENTIALS));
    }

    @Test
    void duplicateSignupUsesTheNormalizedId() {
        when(users.existsByUserId("test")).thenReturn(true);
        assertThatThrownBy(() -> service.signup(new AuthSignupRequest(" test ", "first", "last", "password"), new MockHttpSession()))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getStatus()).isEqualTo(409));
        verify(users).existsByUserId("test"); verify(users, never()).saveAndFlush(any());
    }

    @Test
    void signupUniquenessRaceIsAConflictAndDoesNotLogIn() {
        when(passwords.encode("password")).thenReturn("hash");
        when(users.saveAndFlush(any())).thenThrow(new DataIntegrityViolationException("duplicate", new SQLException("unique", "23505")));
        MockHttpSession session = new MockHttpSession();
        assertThatThrownBy(() -> service.signup(new AuthSignupRequest("test", "first", "last", "password"), session))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getErrorCode()).isEqualTo(ErrorCode.USER_ALREADY_EXISTS));
        assertThat(session.getAttribute(AuthService.SESSION_USER_ID)).isNull();
    }

    private User user(UserRole role) {
        User user = new User(); user.onCreate(); user.setUserId("test"); user.setRole(role); return user;
    }
    private MockHttpSession session(User user) {
        MockHttpSession session = new MockHttpSession(); session.setAttribute(AuthService.SESSION_USER_ID, user.getId()); return session;
    }
}
