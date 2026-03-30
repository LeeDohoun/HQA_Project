package com.hqa.backend.config;

import java.util.ArrayList;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "hqa")
public class HqaProperties {

    private String env = "local";
    private String appVersion = "1.0.0";
    private List<String> corsOrigins = new ArrayList<>(List.of("http://localhost:3000", "http://localhost:8501"));
    private String secretKey = "change-me-in-production";
    private int rateLimitPerMinute = 30;
    private String aiServerUrl = "http://localhost:8001";
    private String redisUrl = "redis://localhost:6379/0";
    private String kisAppKey = "";
    private String kisAppSecret = "";
    private String kisAccountNo = "";
    private String kiwoomAppKey = "";
    private String kiwoomAppSecret = "";

    public String getEnv() {
        return env;
    }

    public void setEnv(String env) {
        this.env = env;
    }

    public String getAppVersion() {
        return appVersion;
    }

    public void setAppVersion(String appVersion) {
        this.appVersion = appVersion;
    }

    public List<String> getCorsOrigins() {
        return corsOrigins;
    }

    public void setCorsOrigins(List<String> corsOrigins) {
        this.corsOrigins = corsOrigins;
    }

    public String getSecretKey() {
        return secretKey;
    }

    public void setSecretKey(String secretKey) {
        this.secretKey = secretKey;
    }

    public int getRateLimitPerMinute() {
        return rateLimitPerMinute;
    }

    public void setRateLimitPerMinute(int rateLimitPerMinute) {
        this.rateLimitPerMinute = rateLimitPerMinute;
    }

    public String getAiServerUrl() {
        return aiServerUrl;
    }

    public void setAiServerUrl(String aiServerUrl) {
        this.aiServerUrl = aiServerUrl;
    }

    public String getRedisUrl() {
        return redisUrl;
    }

    public void setRedisUrl(String redisUrl) {
        this.redisUrl = redisUrl;
    }

    public String getKisAppKey() {
        return kisAppKey;
    }

    public void setKisAppKey(String kisAppKey) {
        this.kisAppKey = kisAppKey;
    }

    public String getKisAppSecret() {
        return kisAppSecret;
    }

    public void setKisAppSecret(String kisAppSecret) {
        this.kisAppSecret = kisAppSecret;
    }

    public String getKisAccountNo() {
        return kisAccountNo;
    }

    public void setKisAccountNo(String kisAccountNo) {
        this.kisAccountNo = kisAccountNo;
    }

    public String getKiwoomAppKey() {
        return kiwoomAppKey;
    }

    public void setKiwoomAppKey(String kiwoomAppKey) {
        this.kiwoomAppKey = kiwoomAppKey;
    }

    public String getKiwoomAppSecret() {
        return kiwoomAppSecret;
    }

    public void setKiwoomAppSecret(String kiwoomAppSecret) {
        this.kiwoomAppSecret = kiwoomAppSecret;
    }
}
