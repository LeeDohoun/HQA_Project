package com.hqa.backend.service;

/**
 * KRX price units (호가가격단위), one table for KOSPI and KOSDAQ since 2023-01-25. KIS refuses an
 * order priced between two units (40030000, 호가단위 오류), and a KIS paper quote can fall there:
 * 373220 was quoted at 406,250 on 2026-10-16, where the unit is 500.
 */
public final class KrxTickSize {
    private KrxTickSize() { }

    /** The price unit at {@code price}. */
    public static long of(long price) {
        if (price <= 0) throw new IllegalArgumentException("A positive price is required");
        if (price < 2_000) return 1;
        if (price < 5_000) return 5;
        if (price < 20_000) return 10;
        if (price < 50_000) return 50;
        if (price < 200_000) return 100;
        if (price < 500_000) return 500;
        return 1_000;
    }

    public static boolean isValid(long price) {
        return price > 0 && price % of(price) == 0;
    }

    /**
     * The lowest valid price at or above {@code price}. Each band starts on a multiple of the
     * previous band's unit, so rounding up past a band's end lands on the next band's first price.
     */
    public static long roundUp(long price) {
        long unit = of(price);
        return Math.floorDiv(price + unit - 1, unit) * unit;
    }

    /** The highest valid price at or below {@code price}. */
    public static long roundDown(long price) {
        long unit = of(price);
        return Math.floorDiv(price, unit) * unit;
    }
}
