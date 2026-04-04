package com.hqa.backend.controller;

import com.hqa.backend.dto.AuthLoginRequest;
import com.hqa.backend.dto.AuthResponse;
import com.hqa.backend.dto.AuthSignupRequest;
import com.hqa.backend.dto.AuthUserResponse;
import com.hqa.backend.dto.UserSecretRequest;
import com.hqa.backend.dto.UserSecretResponse;
import com.hqa.backend.dto.UserSurveyRequest;
import com.hqa.backend.dto.UserSurveyResponse;
import com.hqa.backend.service.AuthService;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    @PostMapping("/signup")
    public AuthResponse signup(@Valid @RequestBody AuthSignupRequest request, HttpSession session) {
        return authService.signup(request, session);
    }

    @PostMapping("/login")
    public AuthResponse login(@Valid @RequestBody AuthLoginRequest request, HttpSession session) {
        return authService.login(request, session);
    }

    @PostMapping("/logout")
    public AuthResponse logout(HttpSession session) {
        return authService.logout(session);
    }

    @GetMapping("/me")
    public AuthUserResponse me(HttpSession session) {
        return authService.getCurrentUser(session);
    }

    @GetMapping("/me/kis")
    public UserSecretResponse getKis(HttpSession session) {
        return authService.getUserSecret(session);
    }

    @PutMapping("/me/kis")
    public UserSecretResponse saveKis(@Valid @RequestBody UserSecretRequest request, HttpSession session) {
        return authService.upsertUserSecret(request, session);
    }

    @GetMapping("/me/survey")
    public UserSurveyResponse getSurvey(HttpSession session) {
        return authService.getSurvey(session);
    }

    @PutMapping("/me/survey")
    public UserSurveyResponse saveSurvey(@Valid @RequestBody UserSurveyRequest request, HttpSession session) {
        return authService.saveSurvey(request, session);
    }
}
