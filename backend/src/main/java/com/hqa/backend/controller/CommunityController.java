package com.hqa.backend.controller;

import com.hqa.backend.dto.CommentListResponse;
import com.hqa.backend.dto.CommentRequest;
import com.hqa.backend.dto.CommentResponse;
import com.hqa.backend.dto.InquiryCreateRequest;
import com.hqa.backend.dto.InquiryResolveRequest;
import com.hqa.backend.dto.PostCreateRequest;
import com.hqa.backend.dto.PostListResponse;
import com.hqa.backend.dto.PostResponse;
import com.hqa.backend.dto.PostUpdateRequest;
import com.hqa.backend.entity.User;
import com.hqa.backend.service.AuthService;
import com.hqa.backend.service.CommunityService;
import jakarta.servlet.http.HttpSession;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/community")
public class CommunityController {

    private final AuthService authService;
    private final CommunityService communityService;

    public CommunityController(AuthService authService, CommunityService communityService) {
        this.authService = authService;
        this.communityService = communityService;
    }

    // 자유게시판

    @GetMapping("/free")
    public PostListResponse listFree(
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size,
            HttpSession session) {
        authService.requireUser(session);
        return communityService.listFree(page, size);
    }

    @PostMapping("/free")
    @ResponseStatus(HttpStatus.CREATED)
    public PostResponse createFree(@Valid @RequestBody PostCreateRequest request, HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.createFree(user, request);
    }

    // 종목토론방

    @GetMapping("/stock")
    public PostListResponse listStock(
            @RequestParam(required = false) String stockCode,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size,
            HttpSession session) {
        authService.requireUser(session);
        return communityService.listStock(stockCode, page, size);
    }

    @PostMapping("/stock")
    @ResponseStatus(HttpStatus.CREATED)
    public PostResponse createStock(@Valid @RequestBody PostCreateRequest request, HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.createStock(user, request);
    }

    // 문의 게시판

    @GetMapping("/inquiries")
    public PostListResponse listInquiries(
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size,
            HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.listInquiries(user, page, size);
    }

    @PostMapping("/inquiries")
    @ResponseStatus(HttpStatus.CREATED)
    public PostResponse createInquiry(@Valid @RequestBody InquiryCreateRequest request, HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.createInquiry(user, request);
    }

    /** 문의 종결. 관리자만 호출할 수 있다. */
    @PatchMapping("/inquiries/{postId}")
    public PostResponse resolveInquiry(
            @PathVariable String postId,
            @Valid @RequestBody InquiryResolveRequest request,
            HttpSession session) {
        User admin = authService.requireAdmin(session);
        return communityService.resolveInquiry(admin, postId, request);
    }

    // 글 공통

    @PostMapping("/posts/{postId}/recommendations")
    @ResponseStatus(HttpStatus.CREATED)
    public PostResponse recommend(@PathVariable String postId, HttpSession session) {
        return communityService.recommend(authService.requireUser(session), postId);
    }

    @GetMapping("/posts/{postId}")
    public PostResponse get(@PathVariable String postId, HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.get(user, postId);
    }

    @PutMapping("/posts/{postId}")
    public PostResponse update(
            @PathVariable String postId,
            @Valid @RequestBody PostUpdateRequest request,
            HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.update(user, postId, request);
    }

    @DeleteMapping("/posts/{postId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String postId, HttpSession session) {
        User user = authService.requireUser(session);
        communityService.delete(user, postId);
    }

    // 댓글

    @GetMapping("/posts/{postId}/comments")
    public CommentListResponse listComments(
            @PathVariable String postId,
            @RequestParam(required = false) String after,
            @RequestParam(defaultValue = "20") int size,
            HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.listComments(user, postId, after, size);
    }

    @GetMapping("/posts/{postId}/comments/visible")
    public CommentListResponse visibleComments(
            @PathVariable String postId,
            @RequestParam List<String> ids,
            HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.visibleComments(user, postId, ids);
    }

    @PostMapping("/posts/{postId}/comments")
    @ResponseStatus(HttpStatus.CREATED)
    public CommentResponse addComment(
            @PathVariable String postId,
            @Valid @RequestBody CommentRequest request,
            HttpSession session) {
        User user = authService.requireUser(session);
        return communityService.addComment(user, postId, request);
    }

    @DeleteMapping("/comments/{commentId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteComment(@PathVariable String commentId, HttpSession session) {
        User user = authService.requireUser(session);
        communityService.deleteComment(user, commentId);
    }
}
