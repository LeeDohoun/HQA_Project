package com.hqa.backend.controller;

import com.hqa.backend.dto.ChatRequest;
import com.hqa.backend.service.AiServerClient;
import jakarta.validation.Valid;
import java.util.HashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/chat")
public class ChatController {

    private final AiServerClient aiServerClient;

    public ChatController(AiServerClient aiServerClient) {
        this.aiServerClient = aiServerClient;
    }

    @PostMapping
    public Map<String, Object> chat(@Valid @RequestBody ChatRequest request) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("message", request.getMessage());
        if (request.getSessionId() != null) payload.put("session_id", request.getSessionId());
        return aiServerClient.chat(payload);
    }
}
