package com.hqa.backend.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.hqa.backend.dto.*;
import com.hqa.backend.entity.*;
import com.hqa.backend.entity.enums.*;
import com.hqa.backend.exception.*;
import com.hqa.backend.repository.*;
import com.hqa.backend.service.*;
import jakarta.servlet.http.HttpSession;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.SliceImpl;
import org.springframework.data.domain.PageRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.MediaType;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class CommunityControllerTest {
    private final AuthService auth = mock(AuthService.class);
    private final CommunityService service = mock(CommunityService.class);
    private final User viewer = mock(User.class);
    private MockMvc mvc;

    @BeforeEach
    void setup() {
        ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule())
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
        mvc = MockMvcBuilders.standaloneSetup(new CommunityController(auth, service))
                .setControllerAdvice(new GlobalExceptionHandler())
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();
        when(auth.requireUser(any(HttpSession.class))).thenReturn(viewer);
    }

    @Test
    void recommendationUsesSessionIdentityAndIgnoresSuppliedUserId() throws Exception {
        mvc.perform(post("/api/v1/community/posts/post/recommendations")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"user_id\":\"other\",\"points\":999}"))
                .andExpect(status().isCreated());
        verify(service).recommend(viewer, "post");
    }

    @Test
    void recommendationCancellationHasNoMutationEndpoint() throws Exception {
        mvc.perform(delete("/api/v1/community/posts/post/recommendations"))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(header().string("Allow", "POST"));
        verifyNoInteractions(auth, service);
    }

    @Test
    void detailsSerializePermissionFlagsInSnakeCase() throws Exception {
        when(viewer.getId()).thenReturn("admin");
        when(viewer.getRole()).thenReturn(UserRole.admin);
        User author = mock(User.class);
        when(author.getId()).thenReturn("owner");
        Post post = new Post();
        post.setId("post");
        post.setUser(author);
        post.setBoardType(BoardType.INQUIRY);
        post.setInquiryStatus(InquiryStatus.OPEN);
        post.onCreate();
        Post target = new Post();
        target.setId("target");
        post.setTargetPost(target);
        Comment comment = new Comment();
        comment.setUser(author);
        comment.setPost(post);
        comment.onCreate();
        PostRepository posts = mock(PostRepository.class);
        CommentRepository comments = mock(CommentRepository.class);
        when(posts.findActiveById("post")).thenReturn(Optional.of(post));
        when(comments.findActiveByPostId(eq("post"), any(Pageable.class))).thenReturn(new SliceImpl<>(List.of(comment)));
        PostResponse response = new CommunityService(posts, comments,
                mock(com.hqa.backend.service.CommunityPointsService.class),
                mock(com.hqa.backend.repository.CommunityPointsRepository.class)).get(viewer, "post");
        when(service.get(viewer, "post")).thenReturn(response);
        mvc.perform(get("/api/v1/community/posts/post"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.editable").value(false))
                .andExpect(jsonPath("$.deletable").value(true))
                .andExpect(jsonPath("$.delete_requestable").value(false))
                .andExpect(jsonPath("$.inquiry_resolvable").value(true))
                .andExpect(jsonPath("$.target_post_deletable").value(true))
                .andExpect(jsonPath("$.comments[0].mine").value(false))
                .andExpect(jsonPath("$.comments[0].deletable").value(true))
                .andExpect(jsonPath("$.has_more_comments").value(false));
    }

    @Test
    void resolutionBindsAnswerAndTargetDeletionUsingAdminSession() throws Exception {
        when(auth.requireAdmin(any(HttpSession.class))).thenReturn(viewer);
        mvc.perform(patch("/api/v1/community/inquiries/inquiry")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"RESOLVED\",\"admin_reply\":\"answer\",\"delete_target_post\":true}"))
                .andExpect(status().isOk());
        ArgumentCaptor<InquiryResolveRequest> request = ArgumentCaptor.forClass(InquiryResolveRequest.class);
        verify(service).resolveInquiry(eq(viewer), eq("inquiry"), request.capture());
        assertThat(request.getValue().status()).isEqualTo(InquiryStatus.RESOLVED);
        assertThat(request.getValue().adminReply()).isEqualTo("answer");
        assertThat(request.getValue().deleteTargetPost()).isTrue();
    }

    @Test
    void resolutionRejectsNonAdminBeforeCallingService() throws Exception {
        when(auth.requireAdmin(any(HttpSession.class)))
                .thenThrow(new ApiException(ErrorCode.FORBIDDEN, 403, "Admin only", null));
        mvc.perform(patch("/api/v1/community/inquiries/inquiry")
                .contentType(MediaType.APPLICATION_JSON).content("{\"status\":\"RESOLVED\"}"))
                .andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }

    @Test
    void editingUsesCurrentSessionAndValidatesInput() throws Exception {
        mvc.perform(put("/api/v1/community/posts/post").contentType(MediaType.APPLICATION_JSON)
                .content("{\"title\":\"edited\",\"content\":\"body\"}"))
                .andExpect(status().isOk());
        verify(service).update(viewer, "post", new PostUpdateRequest("edited", "body"));
        clearInvocations(service);
        mvc.perform(put("/api/v1/community/posts/post").contentType(MediaType.APPLICATION_JSON)
                .content("{\"title\":\" \",\"content\":\"body\"}"))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(service);
    }
    @Test
    void commentListingUsesSessionAndBindsCursorAndSize() throws Exception {
        when(service.listComments(viewer, "post", "cursor", 20))
                .thenReturn(new CommentListResponse(List.of(), "next", true, 100));
        mvc.perform(get("/api/v1/community/posts/post/comments").param("after", "cursor"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.next_cursor").value("next"))
                .andExpect(jsonPath("$.has_more").value(true))
                .andExpect(jsonPath("$.total_items").value(100));
        verify(service).listComments(viewer, "post", "cursor", 20);
    }

    @Test
    void commentListingRequiresLogin() throws Exception {
        when(auth.requireUser(any(HttpSession.class)))
                .thenThrow(new ApiException(ErrorCode.UNAUTHORIZED, 401, "Login required", null));
        mvc.perform(get("/api/v1/community/posts/post/comments"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(service);
    }

    @Test
    void visibleCommentLookupBindsRepeatedIdsAndRequiresSession() throws Exception {
        when(service.visibleComments(viewer, "post", List.of("one", "two")))
                .thenReturn(new CommentListResponse(List.of(), null, false, 5));
        mvc.perform(get("/api/v1/community/posts/post/comments/visible").param("ids", "one", "two"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.total_items").value(5));
        verify(service).visibleComments(viewer, "post", List.of("one", "two"));
        clearInvocations(service);
        when(auth.requireUser(any(HttpSession.class)))
                .thenThrow(new ApiException(ErrorCode.UNAUTHORIZED, 401, "Login required", null));
        mvc.perform(get("/api/v1/community/posts/post/comments/visible").param("ids", "one"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(service);
    }

    @Test
    void visibleCommentLookupRejectsMissingIds() throws Exception {
        mvc.perform(get("/api/v1/community/posts/post/comments/visible"))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(service);
    }

    @Test
    void malformedPageAndJsonReturnClientErrorsInsteadOfServerErrors() throws Exception {
        mvc.perform(get("/api/v1/community/free").param("page", "invalid"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error_code").value("INVALID_REQUEST"));
        mvc.perform(post("/api/v1/community/free").contentType(MediaType.APPLICATION_JSON).content("{invalid"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error_code").value("INVALID_REQUEST"));
        verifyNoInteractions(service);
    }

}
