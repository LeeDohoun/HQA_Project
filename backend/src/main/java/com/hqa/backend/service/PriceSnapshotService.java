package com.hqa.backend.service;

import com.hqa.backend.dto.PriceSnapshotResponse;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.repository.UserRepository;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Service;

@Service
public class PriceSnapshotService {

    private static final Duration CACHE_TTL = Duration.ofSeconds(20);

    private final UserRepository userRepository;
    private final KisClient kisClient;
    private final ConcurrentHashMap<String, CachedSnapshot> cache = new ConcurrentHashMap<>();

    private record CachedSnapshot(PriceSnapshotResponse response, OffsetDateTime expiresAt) {
        boolean isFresh(OffsetDateTime now) {
            return expiresAt.isAfter(now);
        }
    }

    public PriceSnapshotService(UserRepository userRepository, KisClient kisClient) {
        this.userRepository = userRepository;
        this.kisClient = kisClient;
    }

    public List<PriceSnapshotResponse> getSnapshots(String userId, List<String> stockCodes) {
        Set<String> codes = normalizeCodes(stockCodes);
        if (codes.isEmpty()) {
            return List.of();
        }

        Optional<User> userOpt = userRepository.findByUserId(userId);
        if (userOpt.isEmpty()) {
            return failureRows(codes, "USER_NOT_FOUND");
        }

        User user = userOpt.get();
        UserSecret secret = user.getSecret();
        if (!hasKisSecret(secret)) {
            return failureRows(codes, "KIS_SECRET_MISSING");
        }

        OffsetDateTime now = OffsetDateTime.now();
        List<PriceSnapshotResponse> responses = new ArrayList<>();
        List<String> misses = new ArrayList<>();
        for (String code : codes) {
            CachedSnapshot cached = cache.get(cacheKey(userId, code));
            if (cached != null && cached.isFresh(now)) {
                responses.add(cached.response());
            } else {
                misses.add(code);
            }
        }

        if (!misses.isEmpty()) {
            String token = kisClient.fetchAccessToken(userId, secret);
            if (token == null || token.isBlank()) {
                responses.addAll(failureRows(misses, "KIS_TOKEN_UNAVAILABLE"));
            } else {
                for (String code : misses) {
                    responses.add(fetchOne(userId, secret, token, code, now));
                }
            }
        }
        return responses;
    }

    private PriceSnapshotResponse fetchOne(String userId, UserSecret secret, String token, String stockCode, OffsetDateTime now) {
        Long price = kisClient.inquireCurrentPrice(userId, secret, token, stockCode);
        if (price == null || price <= 0) {
            return new PriceSnapshotResponse(stockCode, null, now, "kis", false, "CURRENT_PRICE_UNAVAILABLE");
        }
        PriceSnapshotResponse response = new PriceSnapshotResponse(stockCode, price, now, "kis", true, null);
        cache.put(cacheKey(userId, stockCode), new CachedSnapshot(response, now.plus(CACHE_TTL)));
        return response;
    }

    private static List<PriceSnapshotResponse> failureRows(Iterable<String> stockCodes, String reason) {
        OffsetDateTime now = OffsetDateTime.now();
        List<PriceSnapshotResponse> rows = new ArrayList<>();
        for (String code : stockCodes) {
            rows.add(new PriceSnapshotResponse(code, null, now, "kis", false, reason));
        }
        return rows;
    }

    private static Set<String> normalizeCodes(List<String> stockCodes) {
        Set<String> codes = new LinkedHashSet<>();
        for (String code : stockCodes == null ? List.<String>of() : stockCodes) {
            String normalized = String.valueOf(code == null ? "" : code).trim();
            if (!normalized.isBlank()) {
                codes.add(normalized);
            }
        }
        return codes;
    }

    private static boolean hasKisSecret(UserSecret secret) {
        return secret != null
                && !isBlank(secret.getKisAppKey())
                && !isBlank(secret.getKisAppSecret())
                && !isBlank(secret.getKisAccountNo())
                && !isBlank(secret.getKisAccountProductCode());
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private static String cacheKey(String userId, String stockCode) {
        return userId + ":" + stockCode;
    }
}
