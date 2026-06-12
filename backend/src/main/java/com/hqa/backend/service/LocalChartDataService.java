package com.hqa.backend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.config.HqaProperties;
import com.hqa.backend.dto.CandleData;
import java.io.BufferedReader;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.stream.Stream;
import org.springframework.stereotype.Service;

@Service
public class LocalChartDataService {

    private static final ZoneId KIS_ZONE = ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter ISO_DATE_TIME = DateTimeFormatter.ISO_LOCAL_DATE_TIME;

    private final HqaProperties properties;
    private final ObjectMapper objectMapper;

    public LocalChartDataService(HqaProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    public List<CandleData> fetchDailyCandles(String stockCode, String timeframe, int count, Long before) {
        if (!List.of("1d", "1w", "1M", "1y").contains(timeframe)) {
            return List.of();
        }
        List<CandleData> daily = loadDailyCandles(stockCode, before);
        if (daily.isEmpty()) return List.of();

        List<CandleData> candles = switch (timeframe) {
            case "1w" -> bucketByCalendar(daily, "week");
            case "1M" -> bucketByCalendar(daily, "month");
            case "1y" -> bucketByCalendar(daily, "year");
            default -> daily;
        };
        if (candles.size() > count) {
            return new ArrayList<>(candles.subList(candles.size() - count, candles.size()));
        }
        return candles;
    }

    private List<CandleData> loadDailyCandles(String stockCode, Long before) {
        Path marketRoot = dataDir().resolve("market_data");
        if (!Files.isDirectory(marketRoot)) return List.of();

        Map<Long, CandleData> byTime = new LinkedHashMap<>();
        try (Stream<Path> themes = Files.list(marketRoot)) {
            for (Path chart : themes
                    .map(path -> path.resolve("chart.jsonl"))
                    .filter(Files::exists)
                    .toList()) {
                readChartFile(chart, stockCode, before, byTime);
            }
        } catch (IOException ignored) {
            return List.of();
        }

        return byTime.values().stream()
                .sorted(Comparator.comparingLong(CandleData::time))
                .toList();
    }

    private void readChartFile(Path chart, String stockCode, Long before, Map<Long, CandleData> byTime) {
        try (BufferedReader reader = Files.newBufferedReader(chart)) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isBlank()) continue;
                JsonNode node = objectMapper.readTree(line);
                if (!stockCode.equals(text(node, "stock_code"))) continue;
                CandleData candle = toCandle(node);
                if (candle == null) continue;
                if (before != null && candle.time() >= before) continue;
                byTime.put(candle.time(), candle);
            }
        } catch (IOException ignored) {
            // Keep scanning the remaining theme files.
        }
    }

    private CandleData toCandle(JsonNode node) {
        String timestamp = text(node, "timestamp");
        if (timestamp.isBlank()) return null;
        long time = parseTimestamp(timestamp);
        if (time <= 0) return null;

        double open = parseNumber(text(node, "open"));
        double high = parseNumber(text(node, "high"));
        double low = parseNumber(text(node, "low"));
        double close = parseNumber(text(node, "close"));
        long volume = Math.round(parseNumber(text(node, "volume")));
        if (open <= 0 || high <= 0 || low <= 0 || close <= 0) return null;
        return new CandleData(time, open, high, low, close, volume, Boolean.TRUE);
    }

    private List<CandleData> bucketByCalendar(List<CandleData> daily, String unit) {
        List<CandleData> out = new ArrayList<>();
        String currentKey = "";
        CandleData first = null;
        double high = 0;
        double low = 0;
        CandleData last = null;
        long volume = 0;

        for (CandleData candle : daily) {
            LocalDate date = Instant.ofEpochSecond(candle.time()).atZone(KIS_ZONE).toLocalDate();
            String key = switch (unit) {
                case "week" -> date.getYear() + "-W" + String.format(Locale.ROOT, "%02d", date.get(java.time.temporal.IsoFields.WEEK_OF_WEEK_BASED_YEAR));
                case "year" -> String.valueOf(date.getYear());
                default -> date.getYear() + "-" + String.format(Locale.ROOT, "%02d", date.getMonthValue());
            };
            if (!key.equals(currentKey)) {
                if (first != null && last != null) {
                    out.add(new CandleData(first.time(), first.open(), high, low, last.close(), volume, Boolean.TRUE));
                }
                currentKey = key;
                first = candle;
                high = candle.high();
                low = candle.low();
                volume = 0;
            }
            high = Math.max(high, candle.high());
            low = Math.min(low, candle.low());
            volume += candle.volume();
            last = candle;
        }
        if (first != null && last != null) {
            out.add(new CandleData(first.time(), first.open(), high, low, last.close(), volume, Boolean.TRUE));
        }
        return out;
    }

    private Path dataDir() {
        String configured = properties.getHistoricalDataDir();
        if (configured != null && !configured.isBlank()) {
            return Path.of(configured).toAbsolutePath().normalize();
        }
        Path cwd = Path.of(System.getProperty("user.dir")).toAbsolutePath().normalize();
        Path direct = cwd.resolve("data");
        if (Files.isDirectory(direct)) return direct;
        return cwd.resolve("../data").normalize();
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value == null || value.isNull() ? "" : value.asText("").trim();
    }

    private static double parseNumber(String value) {
        if (value == null || value.isBlank()) return 0;
        try {
            return Double.parseDouble(value.replace(",", "").trim());
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private static long parseTimestamp(String timestamp) {
        try {
            return LocalDateTime.parse(timestamp, ISO_DATE_TIME).atZone(KIS_ZONE).toEpochSecond();
        } catch (Exception ignored) {
            try {
                return LocalDate.parse(timestamp.substring(0, 10)).atStartOfDay(KIS_ZONE).toEpochSecond();
            } catch (Exception ignoredAgain) {
                return 0;
            }
        }
    }
}
