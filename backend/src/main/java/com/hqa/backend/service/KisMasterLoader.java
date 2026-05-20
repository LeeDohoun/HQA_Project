package com.hqa.backend.service;

import com.hqa.backend.entity.Stock;
import com.hqa.backend.repository.StockRepository;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.Charset;
import java.nio.file.Path;
import java.nio.file.Files;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Downloads KIS's KOSPI/KOSDAQ master ZIPs, parses the fixed-width .mst files,
 * and upserts rows into {@code stocks}. Mirrors the official Python
 * sample (koreainvestment/open-trading-api) — encoding is CP949, and the
 * trailing N characters of each row form the Part 2 block (228 for KOSPI,
 * 222 for KOSDAQ). The remaining prefix carries the variable-width Korean
 * name after the fixed 9-byte ticker and 12-byte standard code.
 */
@Service
public class KisMasterLoader {

    private static final Logger log = LoggerFactory.getLogger(KisMasterLoader.class);

    private static final Charset CP949 = Charset.forName("x-windows-949");
    private static final int KOSPI_PART2_LEN = 228;
    private static final int KOSDAQ_PART2_LEN = 222;

    private final StockRepository repository;
    private final String kospiUrl;
    private final String kosdaqUrl;
    private final HttpClient http;

    public KisMasterLoader(
            StockRepository repository,
            @Value("${hqa.kis-master.kospi-url:https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip}") String kospiUrl,
            @Value("${hqa.kis-master.kosdaq-url:https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip}") String kosdaqUrl) {
        this.repository = repository;
        this.kospiUrl = kospiUrl;
        this.kosdaqUrl = kosdaqUrl;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(20))
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    /** Reloads both markets. Existing rows are upserted by primary key. */
    @Transactional
    public int reload() {
        Map<String, Stock> merged = new LinkedHashMap<>();
        merged.putAll(loadMarket(kospiUrl, "KOSPI", KOSPI_PART2_LEN));
        merged.putAll(loadMarket(kosdaqUrl, "KOSDAQ", KOSDAQ_PART2_LEN));

        if (merged.isEmpty()) {
            log.warn("[KisMasterLoader] no rows parsed — leaving stocks untouched");
            return 0;
        }

        // Preserve existing English names if we already have them; KIS master
        // files don't carry English names, so we never overwrite with null.
        Map<String, String> existingEn = new HashMap<>();
        for (Stock existing : repository.findAll()) {
            if (existing.getNameEn() != null) {
                existingEn.put(existing.getCode(), existing.getNameEn());
            }
        }
        for (Stock m : merged.values()) {
            String en = existingEn.get(m.getCode());
            if (en != null) {
                m.setNameEn(en);
            }
        }

        List<Stock> toSave = new ArrayList<>(merged.size());
        toSave.addAll(merged.values());
        repository.saveAll(toSave);
        log.info("[KisMasterLoader] upserted {} stocks (KOSPI+KOSDAQ)", merged.size());
        return merged.size();
    }

    private Map<String, Stock> loadMarket(String url, String market, int part2Len) {
        try {
            byte[] zipBytes = download(url);
            String content = unzipFirstEntryAsString(zipBytes);
            List<Stock> rows = parse(content, market, part2Len);
            Map<String, Stock> indexed = new LinkedHashMap<>();
            for (Stock row : rows) {
                indexed.put(row.getCode(), row);
            }
            log.info("[KisMasterLoader] {} → {} rows", market, indexed.size());
            return indexed;
        } catch (Exception e) {
            log.error("[KisMasterLoader] failed to load {} from {}: {}", market, url, e.toString());
            return Map.of();
        }
    }

    private byte[] download(String url) throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                .timeout(Duration.ofSeconds(60))
                .GET()
                .build();
        HttpResponse<byte[]> res = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
        if (res.statusCode() / 100 != 2) {
            throw new IOException("HTTP " + res.statusCode() + " from " + url);
        }
        return res.body();
    }

    private String unzipFirstEntryAsString(byte[] zipBytes) throws IOException {
        try (ZipInputStream zis = new ZipInputStream(new ByteArrayInputStream(zipBytes))) {
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                if (entry.isDirectory()) continue;
                byte[] data = zis.readAllBytes();
                return new String(data, CP949);
            }
        }
        throw new IOException("zip has no entries");
    }

    /**
     * Parses the .mst file content. Each line: prefix part (ticker, std code,
     * Korean name) followed by Part 2 fixed-width data. We only need ticker
     * and Korean name; everything in Part 2 is discarded.
     */
    List<Stock> parse(String content, String market, int part2Len) {
        List<Stock> out = new ArrayList<>();
        for (String rawLine : content.split("\\r?\\n")) {
            if (rawLine.length() <= part2Len) continue;
            String prefix = rawLine.substring(0, rawLine.length() - part2Len);
            if (prefix.length() < 21) continue;
            String code = prefix.substring(0, 9).trim();
            String nameKo = prefix.substring(21).trim();
            if (code.isEmpty() || nameKo.isEmpty()) continue;
            // KIS uses 6-digit tickers; some lines pad to 9. Keep the raw value
            // but only ingest plausible 6-digit equity codes.
            if (!code.matches("^\\d{6}$")) continue;
            out.add(new Stock(code, nameKo, null, market));
        }
        return out;
    }

    // Test helper — feeds a local file through the parser, no HTTP.
    List<Stock> parseLocalForTest(Path path, String market, int part2Len) throws IOException {
        String content = new String(Files.readAllBytes(path), CP949);
        return parse(content, market, part2Len);
    }

    // Visible for tests / wiring helpers.
    static List<Integer> kospiKosdaqLengths() {
        return Arrays.asList(KOSPI_PART2_LEN, KOSDAQ_PART2_LEN);
    }
}
