package com.hqa.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import jakarta.persistence.Table;
import java.io.Serializable;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.Objects;
import org.hibernate.annotations.Immutable;

/**
 * NAV of a PAPER account after a session's close, one row per session and source: {@code krx_official}
 * (binding, posted by the Python runtime) or {@code snapshot} (the backend's KIS balance read, a cross-check).
 */
@Entity
@Immutable
@IdClass(StrategyNavClose.Key.class)
@Table(name = "strategy_nav_closes")
public class StrategyNavClose {
    public static final String OFFICIAL = "krx_official";
    public static final String SNAPSHOT = "snapshot";
    @Id
    private String accountBinding;
    @Id
    private LocalDate sessionDate;
    @Id
    private String source;
    /** Null only for a snapshot close whose settled cash was unavailable. */
    private Long navKrw;
    private Long cashKrw;
    private Long holdingsValueKrw;
    private String inputHash;
    @Column(nullable = false)
    private OffsetDateTime recordedAt;

    protected StrategyNavClose() { }

    public StrategyNavClose(String accountBinding, LocalDate sessionDate, String source, Long navKrw, Long cashKrw,
            Long holdingsValueKrw, String inputHash, OffsetDateTime recordedAt) {
        this.accountBinding = accountBinding;
        this.sessionDate = sessionDate;
        this.source = source;
        this.navKrw = navKrw;
        this.cashKrw = cashKrw;
        this.holdingsValueKrw = holdingsValueKrw;
        this.inputHash = inputHash;
        this.recordedAt = recordedAt;
    }

    public String getAccountBinding() { return accountBinding; }
    public LocalDate getSessionDate() { return sessionDate; }
    public String getSource() { return source; }
    public Long getNavKrw() { return navKrw; }
    public Long getCashKrw() { return cashKrw; }
    public Long getHoldingsValueKrw() { return holdingsValueKrw; }
    public String getInputHash() { return inputHash; }
    public OffsetDateTime getRecordedAt() { return recordedAt; }

    /** Same content (all posted fields), ignoring when it was recorded. */
    public boolean sameContent(long nav, Long cash, Long holdings, String hash) {
        return navKrw != null && navKrw == nav && Objects.equals(cashKrw, cash) && Objects.equals(holdingsValueKrw, holdings)
                && Objects.equals(inputHash, hash);
    }

    public static class Key implements Serializable {
        private String accountBinding;
        private LocalDate sessionDate;
        private String source;

        public Key() { }

        public Key(String accountBinding, LocalDate sessionDate, String source) {
            this.accountBinding = accountBinding;
            this.sessionDate = sessionDate;
            this.source = source;
        }

        @Override
        public boolean equals(Object other) {
            return other instanceof Key key && Objects.equals(accountBinding, key.accountBinding)
                    && Objects.equals(sessionDate, key.sessionDate) && Objects.equals(source, key.source);
        }

        @Override
        public int hashCode() {
            return Objects.hash(accountBinding, sessionDate, source);
        }
    }
}
