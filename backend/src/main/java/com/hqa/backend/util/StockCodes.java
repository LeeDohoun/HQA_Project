package com.hqa.backend.util;

import java.util.regex.Pattern;

/** KRX short stock codes include uppercase ASCII letters as well as digits. */
public final class StockCodes {
    public static final String REGEX = "[0-9A-Z]{6}";
    public static final Pattern PATTERN = Pattern.compile(REGEX);

    private StockCodes() {
    }

    public static boolean isValid(String value) {
        return value != null && PATTERN.matcher(value).matches();
    }
}
