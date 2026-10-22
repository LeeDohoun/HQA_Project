package com.hqa.backend.controller;

import com.hqa.backend.dto.MyProfileResponse;
import com.hqa.backend.dto.NicknameRequest;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.CommunityPointsService;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/profile")
public class ProfileController {
    private final AuthService auth;
    private final CommunityPointsService points;
    public ProfileController(AuthService auth, CommunityPointsService points) { this.auth = auth; this.points = points; }
    @GetMapping
    public MyProfileResponse me(HttpSession session) { return points.profile(auth.requireUser(session)); }
    @PutMapping("/nickname")
    public MyProfileResponse nickname(@Valid @RequestBody NicknameRequest request, HttpSession session) {
        return points.updateNickname(auth.requireUser(session), request);
    }
}
