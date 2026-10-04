package com.hqa.backend.dto;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.hqa.backend.util.StockCodes;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.time.LocalDate;

@JsonNaming(PropertyNamingStrategies.LowerCamelCaseStrategy.class)
public record InternalMinuteCandleRequest(
        @NotBlank String userId,
        @NotBlank @Pattern(regexp = StockCodes.REGEX) String stockCode,
        @NotNull @JsonFormat(shape = JsonFormat.Shape.STRING, pattern = "yyyy-MM-dd") LocalDate date
) {
}
