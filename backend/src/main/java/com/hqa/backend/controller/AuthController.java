package com.hqa.backend.controller;

import com.hqa.backend.dto.AuthLoginRequest;
import com.hqa.backend.dto.AuthResponse;
import com.hqa.backend.dto.AuthSignupRequest;
import com.hqa.backend.dto.AuthUserResponse;
import com.hqa.backend.dto.KisVerificationResult;
import com.hqa.backend.dto.UserSecretRequest;
import com.hqa.backend.dto.UserSecretResponse;
import com.hqa.backend.dto.UserPreferenceRequest;
import com.hqa.backend.dto.UserPreferenceResponse;
import com.hqa.backend.service.AuthService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "인증 / 사용자", description = "회원가입·로그인·세션, KIS 자격증명 및 투자성향 설문 관리")
@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    @Operation(summary = "회원가입", description = "신규 사용자를 등록하고 가입 즉시 세션 로그인 처리한다. (공개)")
    @PostMapping("/signup")
    public AuthResponse signup(@Valid @RequestBody AuthSignupRequest request, HttpSession session) {
        return authService.signup(request, session);
    }

    @Operation(summary = "로그인", description = "user_id/password로 로그인하고 세션 쿠키를 발급한다. (공개)")
    @PostMapping("/login")
    public AuthResponse login(@Valid @RequestBody AuthLoginRequest request, HttpSession session) {
        return authService.login(request, session);
    }

    @Operation(summary = "로그아웃", description = "현재 세션을 무효화한다.")
    @PostMapping("/logout")
    public AuthResponse logout(HttpSession session) {
        return authService.logout(session);
    }

    @Operation(summary = "현재 사용자 정보", description = "로그인된 사용자의 프로필과 설정 상태(KIS 설정·설문 완료·자동매매 등)를 조회한다.")
    @GetMapping("/me")
    public AuthUserResponse me(HttpSession session) {
        return authService.getCurrentUser(session);
    }

    @Operation(summary = "KIS 자격증명 조회", description = "저장된 한국투자증권(KIS) 자격증명을 마스킹된 형태로 조회한다.")
    @GetMapping("/me/kis")
    public UserSecretResponse getKis(HttpSession session) {
        return authService.getUserSecret(session);
    }

    @Operation(summary = "KIS 자격증명 저장/수정", description = "한국투자증권(KIS) App Key/Secret/계좌번호를 암호화하여 저장하거나 갱신한다.")
    @PutMapping("/me/kis")
    public UserSecretResponse saveKis(@Valid @RequestBody UserSecretRequest request, HttpSession session) {
        return authService.upsertUserSecret(request, session);
    }

    /**
     * 저장 없이 KIS 자격증명만 검증.
     * 온보딩 위저드에서 "다음" 누르기 전에 호출.
     */
    @Operation(summary = "KIS 자격증명 검증",
            description = "저장하지 않고 KIS 자격증명의 유효성(토큰 발급·계좌 조회)만 검증한다. 온보딩 위저드용.")
    @PostMapping("/me/kis/verify")
    public KisVerificationResult verifyKis(@Valid @RequestBody UserSecretRequest request, HttpSession session) {
        return authService.verifyKisCredentials(request, session);
    }

    @Operation(summary = "투자성향 설문 조회", description = "사용자의 투자성향 설문 응답을 조회한다.")
    @GetMapping("/me/preference")
    public UserPreferenceResponse getPreference(HttpSession session) {
        return authService.getPreference(session);
    }

    @Operation(summary = "투자성향 설문 저장", description = "총 재산·투자 기간·목표 수익률·위험 성향 등 투자성향 설문을 저장한다.")
    @PutMapping("/me/preference")
    public UserPreferenceResponse savePreference(@Valid @RequestBody UserPreferenceRequest request, HttpSession session) {
        return authService.savePreference(request, session);
    }
}
