package com.hqa.backend.entity;

import com.hqa.backend.entity.enums.UserRole;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.OneToMany;
import jakarta.persistence.OneToOne;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@Entity
@Table(name = "users")
public class User {

    @Id
    private String id = UUID.randomUUID().toString();

    @Column(unique = true)
    private String email;
    private String name;

    @Enumerated(EnumType.STRING)
    private UserRole role = UserRole.user;
    private boolean isActive = true;
    private OffsetDateTime createdAt = OffsetDateTime.now();
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @OneToOne(mappedBy = "user")
    private UserCredential credential;

    @OneToMany(mappedBy = "user")
    private List<AnalysisRecord> analyses = new ArrayList<>();

    public String getId() { return id; }
}
