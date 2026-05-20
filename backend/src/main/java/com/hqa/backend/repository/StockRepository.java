package com.hqa.backend.repository;

import com.hqa.backend.entity.Stock;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface StockRepository extends JpaRepository<Stock, String> {

    Optional<Stock> findByCode(String code);

    /** Tradable stocks that operators have whitelisted for auto-trading. */
    List<Stock> findAllByAutoTradeEligibleTrueAndTradableTrue();

    /**
     * Substring match against Korean and English names, plus exact-code match.
     * Backed by GIN trigram indexes on lower(name_ko) / lower(name_en) — see
     * V3__stocks.sql. Falls back to plain LIKE if pg_trgm is unavailable
     * (still fast at ~thousands of rows).
     */
    @Query(value = """
            SELECT * FROM stocks
            WHERE is_tradable = true
              AND (
                    lower(name_ko) LIKE '%' || lower(:term) || '%'
                 OR lower(coalesce(name_en, '')) LIKE '%' || lower(:term) || '%'
                 OR code = :term
              )
            ORDER BY
              CASE
                WHEN code = :term THEN 0
                WHEN lower(name_ko) = lower(:term) THEN 1
                WHEN lower(name_ko) LIKE lower(:term) || '%' THEN 2
                ELSE 3
              END,
              name_ko
            """,
            nativeQuery = true)
    List<Stock> searchByTerm(@Param("term") String term, Pageable pageable);
}
