package com.hqa.backend.controller;

import com.hqa.backend.dto.ChatRequest;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.AuthService;
import jakarta.servlet.http.HttpSession;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.util.HashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "챗봇", description = "AI 챗봇 대화 (로그인 필요)")
@RestController
@RequestMapping("/api/v1/chat")
public class ChatController {

    private final AiServerClient aiServerClient;

    private final AuthService authService;

    public ChatController(AiServerClient aiServerClient, AuthService authService) {
        this.authService = authService;
        this.aiServerClient = aiServerClient;
    }

    @Operation(summary = "AI 챗봇 대화",
            description = "메시지를 AI 서버로 전달해 챗봇 응답을 받는다. session_id로 대화 맥락을 이어갈 수 있다.")
    @PostMapping
    public Map<String, Object> chat(@Valid @RequestBody ChatRequest request, HttpSession session) {
        authService.requireUser(session);
        Map<String, Object> payload = new HashMap<>();
        payload.put("message", request.getMessage());
        if (request.getSessionId() != null) payload.put("session_id", request.getSessionId());
        return aiServerClient.chat(payload);
    }
}
