package com.hqa.backend.repository;

import com.hqa.backend.entity.AccountEquityPeak;
import java.time.OffsetDateTime;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.transaction.annotation.Transactional;

public interface AccountEquityPeakRepository extends JpaRepository<AccountEquityPeak, String> {
    /** Atomic peak = max(peak, equity); concurrent evaluations never lower a stored peak. */
    @Modifying
    @Transactional
    @Query(value = "INSERT INTO public.account_equity_peaks(account_binding, peak_equity_krw, updated_at) "
            + "VALUES (:binding, :equity, :now) ON CONFLICT (account_binding) DO UPDATE SET "
            + "peak_equity_krw = GREATEST(public.account_equity_peaks.peak_equity_krw, EXCLUDED.peak_equity_krw), "
            + "updated_at = EXCLUDED.updated_at", nativeQuery = true)
    int raisePeak(String binding, long equity, OffsetDateTime now);
}
