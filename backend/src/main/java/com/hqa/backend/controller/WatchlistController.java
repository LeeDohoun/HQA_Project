package com.hqa.backend.controller;

import com.hqa.backend.dto.WatchlistItemRequest;
import com.hqa.backend.dto.WatchlistItemResponse;
import com.hqa.backend.dto.WatchlistResponse;
import com.hqa.backend.entity.User;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.WatchlistService;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/watchlist")
public class WatchlistController {

    private final AuthService authService;
    private final WatchlistService watchlistService;

    public WatchlistController(AuthService authService, WatchlistService watchlistService) {
        this.authService = authService;
        this.watchlistService = watchlistService;
    }

    @GetMapping
    public WatchlistResponse list(HttpSession session) {
        User user = authService.requireUser(session);
        return watchlistService.list(user);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public WatchlistItemResponse add(@Valid @RequestBody WatchlistItemRequest request, HttpSession session) {
        User user = authService.requireUser(session);
        return watchlistService.add(user, request);
    }

    @DeleteMapping("/{stockCode}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String stockCode, HttpSession session) {
        User user = authService.requireUser(session);
        watchlistService.delete(user, stockCode);
    }
}
