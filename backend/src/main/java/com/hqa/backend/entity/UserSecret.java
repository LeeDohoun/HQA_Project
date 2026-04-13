package com.hqa.backend.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.OneToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

@Entity
@Table(name = "user_secrets")
public class UserSecret {

    @Id
    private String id;

    private String kisAppKey;
    private String kisAppSecret;
    private String kisAccountNo;
    private OffsetDateTime createdAt = OffsetDateTime.now();
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false, unique = true)
    private User user;

    @PrePersist
    public void onCreate() {
        if (id == null) {
            id = UUID.randomUUID().toString();
        }
        OffsetDateTime now = OffsetDateTime.now();
        createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    public void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }

    public String getId() {
        return id;
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

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public User getUser() {
        return user;
    }

    public void setUser(User user) {
        this.user = user;
    }
}
