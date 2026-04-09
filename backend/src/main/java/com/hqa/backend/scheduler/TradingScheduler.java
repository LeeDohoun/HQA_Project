package com.hqa.backend.scheduler;

import com.hqa.backend.dto.RecommendItem;
import com.hqa.backend.entity.enums.EventType;
import com.hqa.backend.entity.User;
import com.hqa.backend.entity.UserSecret;
import com.hqa.backend.entity.UserPreference;
import com.hqa.backend.repository.UserRepository;
import com.hqa.backend.service.AiServerClient;
import com.hqa.backend.service.KisClient;
import com.hqa.backend.service.ErrorLogger;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class TradingScheduler {

    private static final Logger log = LoggerFactory.getLogger(TradingScheduler.class);

    private final UserRepository userRepository;
    private final AiServerClient aiServerClient;
    private final KisClient kisClient;
    private final ErrorLogger errorLogger;

    public TradingScheduler(UserRepository userRepository, AiServerClient aiServerClient,
                             KisClient kisClient, ErrorLogger errorLogger) {
        this.userRepository = userRepository;
        this.aiServerClient = aiServerClient;
        this.kisClient = kisClient;
        this.errorLogger = errorLogger;
    }

    @Scheduled(fixedRate = 1_800_000)
    public void run() {
        List<User> users = userRepository.findAllActiveWithSecretAndSurvey();
        log.info("[TradingScheduler] Starting scheduled run for {} users", users.size());

        for (User user : users) {
            UserSecret secret = user.getSecret();
            UserPreference preference = user.getPreference();

            if (secret == null || preference == null
                    || isBlank(secret.getKisAppKey())
                    || isBlank(secret.getKisAppSecret())
                    || isBlank(secret.getKisAccountNo())) {
                log.info("[TradingScheduler] Skipping user {} — KIS not configured", user.getUserId());
                continue;
            }

            List<RecommendItem> recommendations;
            try {
                recommendations = aiServerClient.recommend(EventType.RECOMMEND);
            } catch (Exception e) {
                errorLogger.log("TradingScheduler", user.getUserId(), null,
                        "AI server recommend call failed", e.getMessage());
                continue;
            }

            if (recommendations.isEmpty()) {
                log.info("[TradingScheduler] No recommendations for user {}", user.getUserId());
                continue;
            }

            String token = kisClient.fetchAccessToken(user.getUserId(), secret);
            if (token == null) {
                continue;
            }

            log.info("[TradingScheduler] Placing {} orders for user {}", recommendations.size(), user.getUserId());
            for (RecommendItem item : recommendations) {
                log.info("[TradingScheduler] Placing order for user {}: {} qty={} price={}",
                        user.getUserId(), item.stockCode(), item.quantity(), item.limitPrice());
                kisClient.placeLimitOrder(user.getUserId(), secret, token, item);
            }
        }

        log.info("[TradingScheduler] Scheduled run complete");
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
