package com.hqa.backend.entity;

import com.hqa.backend.entity.enums.UserRole;
import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.OneToMany;
import jakarta.persistence.OneToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@Entity
@Table(name = "users")
public class User {

    @Id
    private String id;

    @Column(nullable = false, unique = true)
    private String userId;

    @Column(nullable = false)
    private String firstName;

    @Column(nullable = false)
    private String lastName;

    @Column(nullable = false)
    private String password;

    @Enumerated(EnumType.STRING)
    private UserRole role = UserRole.user;

    @Column(name = "is_active")
    private boolean active = true;

    @Column(name = "auto_trade_enabled", nullable = false,
            columnDefinition = "BOOLEAN NOT NULL DEFAULT false")
    private boolean autoTradeEnabled = false;

    private OffsetDateTime createdAt = OffsetDateTime.now();
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @OneToOne(mappedBy = "user", cascade = CascadeType.ALL, orphanRemoval = true)
    private UserSecret secret;

    @OneToOne(mappedBy = "user", cascade = CascadeType.ALL, orphanRemoval = true)
    private UserPreference preference;

    @OneToMany(mappedBy = "user")
    private List<AnalysisRecord> analyses = new ArrayList<>();

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

    public String getUserId() {
        return userId;
    }

    public void setUserId(String userId) {
        this.userId = userId;
    }

    public String getFirstName() {
        return firstName;
    }

    public void setFirstName(String firstName) {
        this.firstName = firstName;
    }

    public String getLastName() {
        return lastName;
    }

    public void setLastName(String lastName) {
        this.lastName = lastName;
    }

    public String getPassword() {
        return password;
    }

    public void setPassword(String password) {
        this.password = password;
    }

    public UserRole getRole() {
        return role;
    }

    public void setRole(UserRole role) {
        this.role = role;
    }

    public boolean isActive() {
        return active;
    }

    public void setActive(boolean active) {
        this.active = active;
    }

    public boolean isAutoTradeEnabled() {
        return autoTradeEnabled;
    }

    public void setAutoTradeEnabled(boolean autoTradeEnabled) {
        this.autoTradeEnabled = autoTradeEnabled;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public UserSecret getSecret() {
        return secret;
    }

    public void setSecret(UserSecret secret) {
        this.secret = secret;
        if (secret != null) {
            secret.setUser(this);
        }
    }

    public UserPreference getPreference() {
        return preference;
    }

    public void setPreference(UserPreference preference) {
        this.preference = preference;
        if (preference != null) {
            preference.setUser(this);
        }
    }

    public List<AnalysisRecord> getAnalyses() {
        return analyses;
    }
}
