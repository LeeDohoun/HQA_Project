package com.hqa.backend.entity;

import static org.assertj.core.api.Assertions.*;

import jakarta.persistence.Column;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;

/**
 * Offline check that the strategy entities and the V15+V16 migrations name the same columns, as Hibernate's
 * {@code ddl-auto: validate} would require (Postgres itself is exercised only with HQA_TEST_DATABASE_URL).
 */
class StrategySchemaMappingTest {
    private static final List<Class<?>> ENTITIES = List.of(StrategyAccount.class, StrategyBatch.class, StrategyBatchOrder.class,
            StrategyStatusSnapshot.class, AccountEquityPeak.class, TradingHalt.class, StrategyBatchSizing.class,
            StrategyNavClose.class);

    private static Map<String, Set<String>> columnsByTable() throws Exception {
        String sql = new ClassPathResource("db/migration/V15__strategy_runtime.sql").getContentAsString(StandardCharsets.UTF_8)
                + "\n" + new ClassPathResource("db/migration/V16__strategy_portfolio_runtime.sql").getContentAsString(StandardCharsets.UTF_8);
        sql = sql.replaceAll("--[^\n]*", "");
        Map<String, Set<String>> tables = new HashMap<>();
        Matcher create = Pattern.compile("CREATE TABLE public\\.(\\w+) \\((.*?)\\n\\);", Pattern.DOTALL).matcher(sql);
        while (create.find()) {
            Set<String> columns = tables.computeIfAbsent(create.group(1), ignored -> new LinkedHashSet<>());
            for (String line : create.group(2).split("\n")) {
                Matcher column = Pattern.compile("^\\s*(\\w+)\\s+(varchar|uuid|bigint|integer|boolean|date|timestamptz|text|numeric|double)")
                        .matcher(line);
                if (column.find()) columns.add(column.group(1));
            }
        }
        for (String statement : sql.split(";")) {
            Matcher alter = Pattern.compile("ALTER TABLE public\\.(\\w+)").matcher(statement);
            if (!alter.find()) continue;
            Matcher added = Pattern.compile("ADD COLUMN (\\w+)").matcher(statement);
            while (added.find()) tables.computeIfAbsent(alter.group(1), ignored -> new LinkedHashSet<>()).add(added.group(1));
        }
        return tables;
    }

    /** Spring's CamelCaseToUnderscoresNamingStrategy. */
    private static String physical(String name) {
        StringBuilder builder = new StringBuilder(name.replace('.', '_'));
        for (int i = 1; i < builder.length() - 1; i++) {
            if (Character.isLowerCase(builder.charAt(i - 1)) && Character.isUpperCase(builder.charAt(i))
                    && Character.isLowerCase(builder.charAt(i + 1))) {
                builder.insert(i++, '_');
            }
        }
        return builder.toString().toLowerCase();
    }

    private static Set<String> mapped(Class<?> entity) {
        Set<String> columns = new LinkedHashSet<>();
        for (Field field : entity.getDeclaredFields()) {
            if (Modifier.isStatic(field.getModifiers()) || field.isAnnotationPresent(Transient.class)) continue;
            Column column = field.getAnnotation(Column.class);
            columns.add(column != null && !column.name().isEmpty() ? column.name() : physical(field.getName()));
        }
        return columns;
    }

    @Test
    void everyMappedFieldHasAColumnAndEveryColumnIsMapped() throws Exception {
        Map<String, Set<String>> tables = columnsByTable();
        for (Class<?> entity : ENTITIES) {
            String table = entity.getAnnotation(Table.class).name();
            assertThat(tables).as(table).containsKey(table);
            assertThat(tables.get(table)).as(table).containsExactlyInAnyOrderElementsOf(mapped(entity));
        }
        assertThat(tables.get("strategy_nav_closes")).contains("cash_krw", "holdings_value_krw", "source", "input_hash");
        assertThat(tables.get("strategy_accounts")).contains("activated_at", "activation_nav_krw", "terminated_at", "termination_reason");
        assertThat(tables.get("strategy_batch_orders")).contains("planned_quantity", "ref_price_at_submit");
    }

    @Test
    void v16WidensThePhaseForTheContinuousSessionPhases() throws Exception {
        String v16 = new ClassPathResource("db/migration/V16__strategy_portfolio_runtime.sql").getContentAsString(StandardCharsets.UTF_8);
        assertThat(v16).contains("ALTER COLUMN phase TYPE varchar(16)")
                .contains("CHECK (phase IN ('OPEN', 'CLOSE', 'CONT_SELL', 'CONT_BUY'))")
                .contains("PRIMARY KEY (account_binding, session_date, source)");
    }
}
