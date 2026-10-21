package com.hqa.backend.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import java.time.LocalDate;

/** Binding close NAV of a session from the Python runtime (official unadjusted KRX close); source "krx_official". */
@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record StrategyNavCloseRequest(LocalDate sessionDate, Long navKrw, Long cashKrw, Long holdingsValueKrw, String source,
        String inputHash) { }
