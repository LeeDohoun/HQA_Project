package com.hqa.backend.entity;

import jakarta.persistence.Column;
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

    // KIS credentials are stored AES-GCM encrypted (see SecretCipher). The
    // Base64-encoded ciphertext can exceed varchar(255), so map these to TEXT.
    @Column(columnDefinition = "text")
    private String kisAppKey;
    @Column(columnDefinition = "text")
    private String kisAppSecret;
    @Column(columnDefinition = "text")
    private String kisAccountNo;
    @Column(columnDefinition = "text")
    private String kisAccountProductCode;
    // columnDefinition으로 NOT NULL + DEFAULT를 명시해야, 기존 행이 있는 테이블에
    // 이 컬럼을 새로 추가하는 ALTER가 실패하지 않음 (Hibernate ddl-auto: update 한계).
    @Column(nullable = false, columnDefinition = "boolean default false")
    private boolean kisIsReal = false; // true = 실전투자, false = 모의투자
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

    public String getKisAccountProductCode() {
        return kisAccountProductCode;
    }

    public void setKisAccountProductCode(String kisAccountProductCode) {
        this.kisAccountProductCode = kisAccountProductCode;
    }

    public boolean isKisIsReal() {
        return kisIsReal;
    }

    public void setKisIsReal(boolean kisIsReal) {
        this.kisIsReal = kisIsReal;
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
