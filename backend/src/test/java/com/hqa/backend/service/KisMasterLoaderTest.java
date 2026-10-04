package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

class KisMasterLoaderTest {
    private final KisMasterLoader loader = new KisMasterLoader(null, "https://unused.test/kospi", "https://unused.test/kosdaq");
    @TempDir Path directory;

    @ParameterizedTest
    @CsvSource({"0015G0,KOSPI,228", "005930,KOSPI,228", "0015G0,KOSDAQ,222", "005930,KOSDAQ,222"})
    void parsesPaddedShortCodesAndKoreanNamesFromCp949(String code, String market, int part2Len) throws Exception {
        Path file = directory.resolve("market.mst");
        Files.writeString(file, line(code, "그린광학", part2Len) + "\r\n", Charset.forName("x-windows-949"));
        var stocks = loader.parseLocalForTest(file, market, part2Len);
        assertThat(stocks).hasSize(1);
        assertThat(stocks.get(0).getCode()).isEqualTo(code);
        assertThat(stocks.get(0).getNameKo()).isEqualTo("그린광학");
        assertThat(stocks.get(0).getMarket()).isEqualTo(market);
    }

    @ParameterizedTest
    @ValueSource(strings = {"0015g0", "15G0", "0015G0X", "0015-0"})
    void skipsInvalidShortCodesInBothMasterLayouts(String code) {
        for (int length : KisMasterLoader.kospiKosdaqLengths()) {
            assertThat(loader.parse(line(code, "종목", length), "KOSDAQ", length)).isEmpty();
        }
    }

    @Test
    void stillRequiresTheKoreanName() {
        assertThat(loader.parse(line("0015G0", "", 222), "KOSDAQ", 222)).isEmpty();
    }

    private String line(String code, String name, int part2Len) {
        return String.format("%-9s", code) + "KR7" + "0".repeat(9) + name + " ".repeat(10) + "0".repeat(part2Len);
    }
}
