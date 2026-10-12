package com.hqa.backend.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.hqa.backend.controller.AuthController;
import com.hqa.backend.controller.CommunityController;
import com.hqa.backend.controller.ProfileController;
import com.hqa.backend.dto.*;
import com.hqa.backend.entity.User;
import com.hqa.backend.exception.*;
import com.hqa.backend.service.*;
import jakarta.servlet.http.HttpSession;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.web.csrf.CsrfTokenRepository;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

class CsrfSecurityTest {
    private final AuthService auth = mock(AuthService.class);
    private final CommunityService community = mock(CommunityService.class);
    private final CommunityPointsService points = mock(CommunityPointsService.class);
    private final User viewer = mock(User.class);
    private final ObjectMapper mapper = new ObjectMapper().setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
    private MockMvc mvc;

    @BeforeEach
    void setup() {
        CsrfSecurityConfig config = new CsrfSecurityConfig();
        CsrfTokenRepository repository = config.csrfTokenRepository();
        mvc = MockMvcBuilders.standaloneSetup(new AuthController(auth, repository), new CommunityController(auth, community), new ProfileController(auth, points), new InternalProbe())
                .setControllerAdvice(new GlobalExceptionHandler())
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper))
                .addFilters(config.apiCacheControl().getFilter(), config.csrfFilter(repository, mapper).getFilter()).build();
        when(auth.requireUser(any())).thenReturn(viewer);
        when(auth.requireAdmin(any())).thenReturn(viewer);
        when(auth.login(any(), any())).thenAnswer(call -> {
            HttpSession session = call.getArgument(1); session.setAttribute(AuthService.SESSION_USER_ID, "user");
            return new AuthResponse(true, "Logged in", null);
        });
        when(auth.signup(any(), any())).thenAnswer(call -> {
            HttpSession session = call.getArgument(1); session.setAttribute(AuthService.SESSION_USER_ID, "user");
            return new AuthResponse(true, "Signed up", null);
        });
    }

    @Test
    void tokenBootstrapIsAnonymousAndNotCacheable() throws Exception {
        var result = mvc.perform(get("/api/v1/auth/csrf")).andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store")).andReturn();
        assertThat(mapper.readTree(result.getResponse().getContentAsString()).path("token").asText()).hasSizeGreaterThan(40);
        assertThat(result.getRequest().getSession(false)).isNotNull();
        verifyNoInteractions(auth, community);
    }

    @Test
    void everyUnsafeCommunityMethodRejectsMissingTokenBeforeMutation() throws Exception {
        var requests = new org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder[] {
                post("/api/v1/community/free").content("{\"title\":\"title\",\"content\":\"body\"}"),
                put("/api/v1/community/posts/post").content("{\"title\":\"title\",\"content\":\"body\"}"),
                delete("/api/v1/community/comments/comment"),
                post("/api/v1/community/posts/post/recommendations"),
                put("/api/v1/profile/nickname").content("{\"nickname\":\"nickname\"}"),
                patch("/api/v1/community/inquiries/post").content("{\"status\":\"RESOLVED\"}") };
        for (var request : requests) mvc.perform(request.contentType(MediaType.APPLICATION_JSON))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error_code").value("CSRF_INVALID"));
        verifyNoInteractions(auth, community);
    }

    @Test
    void aValidSessionTokenAllowsTheMutation() throws Exception {
        MockHttpSession session = new MockHttpSession(); String token = token(session);
        mvc.perform(post("/api/v1/community/free").session(session).header("X-CSRF-Token", token)
                .contentType(MediaType.APPLICATION_JSON).content("{\"title\":\"title\",\"content\":\"body\"}"))
                .andExpect(status().isCreated());
        verify(community).createFree(viewer, new PostCreateRequest("title", "body", null));
    }

    @Test
    void anotherSessionsTokenCannotBeReplayed() throws Exception {
        String token = token(new MockHttpSession()); MockHttpSession other = new MockHttpSession(); token(other);
        mvc.perform(delete("/api/v1/community/comments/comment").session(other).header("X-CSRF-Token", token))
                .andExpect(status().isForbidden());
        verifyNoInteractions(community);
    }

    @Test
    void loginRenewsTheIdAndInvalidatesThePreLoginToken() throws Exception {
        MockHttpSession session = new MockHttpSession(); String before = session.getId(); String token = token(session);
        mvc.perform(post("/api/v1/auth/login").session(session).header("X-CSRF-Token", token)
                .contentType(MediaType.APPLICATION_JSON).content("{\"user_id\":\"test\",\"password\":\"password\"}"))
                .andExpect(status().isOk());
        assertThat(session.getId()).isNotEqualTo(before);
        assertThat(session.getAttribute(AuthService.SESSION_USER_ID)).isEqualTo("user");
        mvc.perform(delete("/api/v1/community/comments/comment").session(session).header("X-CSRF-Token", token))
                .andExpect(status().isForbidden());
        mvc.perform(delete("/api/v1/community/comments/comment").session(session).header("X-CSRF-Token", token(session)))
                .andExpect(status().isNoContent());
    }

    @Test
    void signupAlsoRenewsTheIdAndToken() throws Exception {
        MockHttpSession session = new MockHttpSession(); String before = session.getId(); String token = token(session);
        mvc.perform(post("/api/v1/auth/signup").session(session).header("X-CSRF-Token", token)
                .contentType(MediaType.APPLICATION_JSON).content("{\"user_id\":\"test\",\"first_name\":\"Test\",\"last_name\":\"User\",\"password\":\"password\"}"))
                .andExpect(status().isOk());
        assertThat(session.getId()).isNotEqualTo(before);
        mvc.perform(delete("/api/v1/community/comments/comment").session(session).header("X-CSRF-Token", token))
                .andExpect(status().isForbidden());
    }

    @Test
    void failedLoginKeepsTheAnonymousSessionAndToken() throws Exception {
        MockHttpSession session = new MockHttpSession(); String before = session.getId(); String token = token(session);
        doThrow(new ApiException(ErrorCode.INVALID_CREDENTIALS, 401, "Invalid credentials", null)).when(auth).login(any(), any());
        mvc.perform(post("/api/v1/auth/login").session(session).header("X-CSRF-Token", token)
                .contentType(MediaType.APPLICATION_JSON).content("{\"user_id\":\"test\",\"password\":\"password\"}"))
                .andExpect(status().isUnauthorized());
        assertThat(session.getId()).isEqualTo(before);
        assertThat(session.getAttribute(AuthService.SESSION_USER_ID)).isNull();
    }

    @Test
    void internalServiceCallsKeepTheirSeparateAuthenticationContract() throws Exception {
        mvc.perform(post("/api/v1/internal/probe").servletPath("/api/v1/internal/probe")).andExpect(status().isOk());
        mvc.perform(post("/api/v1/community/free").header("X-HQA-Internal-Token", "anything"))
                .andExpect(status().isForbidden());
        verifyNoInteractions(community);
    }

    @Test
    void safeReadsDoNotRequireACsrfToken() throws Exception {
        mvc.perform(get("/api/v1/community/posts/post/comments")).andExpect(status().isOk());
        verify(community).listComments(viewer, "post", null, 20);
    }

    @Test
    void sessionReadsAndDeniedWritesAreNotCacheable() throws Exception {
        mvc.perform(get("/api/v1/auth/me")).andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"));
        mvc.perform(get("/api/v1/community/posts/post/comments")).andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"));
        mvc.perform(post("/api/v1/community/free")).andExpect(status().isForbidden())
                .andExpect(header().string("Cache-Control", "no-store"));
    }

    @Test
    void normalizedPublicPathsCannotUseTheInternalException() throws Exception {
        for (String path : java.util.List.of("/api/v1/internal/../community/free",
                "/api/v1/internal/%2e%2e/community/free", "/api/v1/internal/..;/community/free")) {
            var request = new org.springframework.mock.web.MockHttpServletRequest("POST", path);
            request.setServletPath("/api/v1/community/free");
            var response = new org.springframework.mock.web.MockHttpServletResponse();
            var config = new CsrfSecurityConfig();
            var filter = config.csrfFilter(config.csrfTokenRepository(), mapper).getFilter();
            filter.doFilter(request, response, (req, res) -> { throw new AssertionError("Public write bypassed CSRF"); });
            assertThat(response.getStatus()).isEqualTo(403);
        }
    }

    private String token(MockHttpSession session) throws Exception {
        var result = mvc.perform(get("/api/v1/auth/csrf").session(session)).andExpect(status().isOk()).andReturn();
        return mapper.readTree(result.getResponse().getContentAsString()).path("token").asText();
    }
    @RestController
    static class InternalProbe {
        @PostMapping("/api/v1/internal/probe") Map<String, Boolean> probe() { return Map.of("ok", true); }
    }
}
