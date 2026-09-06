package com.hqa.backend.service;

import java.time.LocalTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The v2 condition language is an OR of named AND groups, without arbitrary expressions. */
public final class TradeConditions {
    public enum TriggerType { ENTRY, EXIT, REDUCE, INVALIDATION }
    public record Predicate(String field, String operator, Object value) { }
    public record Group(String id, List<Predicate> all, Double reduceFraction) { }
    private static final Set<String> FIELDS = Set.of("current_price", "pnl_rate", "holding_quantity", "market_time");
    private static final Set<String> OPERATORS = Set.of(">", ">=", "<", "<=", "==", "!=");

    private TradeConditions() { }

    public static boolean isV2(Map<String, Object> payload) {
        Object version = payload.get("schema_version");
        if (version == null) return false;
        if (!(version instanceof Number number) || number.doubleValue() != 2.0) {
            throw new IllegalArgumentException("Unsupported condition schema_version");
        }
        return true;
    }

    public static void validate(Map<String, Object> payload) {
        if (payload == null) throw new IllegalArgumentException("conditionPayload is required");
        isV2(payload);
        for (TriggerType type : TriggerType.values()) groups(payload, type);
    }

    public static List<Group> groups(Map<String, Object> payload, TriggerType type) {
        boolean v2 = isV2(payload);
        String prefix = type.name().toLowerCase(java.util.Locale.ROOT);
        Object raw = payload.get(prefix + "_conditions");
        if (raw == null) return List.of();
        if (!(raw instanceof List<?> rows)) throw new IllegalArgumentException("Conditions must be arrays");
        List<Group> groups = new ArrayList<>();
        Set<String> ids = new HashSet<>();
        for (int i = 0; i < rows.size(); i++) {
            if (!(rows.get(i) instanceof Map<?, ?> row)) throw new IllegalArgumentException("Invalid condition group");
            String id = v2 ? String.valueOf(row.getOrDefault("id", null)) : "legacy-" + prefix + "-" + i;
            if (id == null || !id.matches("[A-Za-z0-9_-]{1,100}") || id.equals("null") || !ids.add(id)) {
                throw new IllegalArgumentException("Condition group IDs must be unique and nonblank");
            }
            Object predicates = v2 ? row.get("all") : List.of(row);
            if (!(predicates instanceof List<?> all) || all.isEmpty()) {
                throw new IllegalArgumentException("Condition group all must be nonempty");
            }
            List<Predicate> parsed = new ArrayList<>();
            for (Object item : all) {
                if (!(item instanceof Map<?, ?> p)) throw new IllegalArgumentException("Invalid predicate");
                String field = String.valueOf(p.get("field"));
                String operator = String.valueOf(p.get("operator"));
                Object value = p.get("value");
                if (!FIELDS.contains(field) || !OPERATORS.contains(operator)) {
                    throw new IllegalArgumentException("Unsupported condition field or operator");
                }
                if (field.equals("market_time")) {
                    if (!(value instanceof String time)) throw new IllegalArgumentException("market_time must be a time");
                    try { LocalTime.parse(time); }
                    catch (java.time.DateTimeException ex) { throw new IllegalArgumentException("Invalid market_time", ex); }
                } else {
                    double number = number(value);
                    if ((field.equals("current_price") && number <= 0)
                            || (field.equals("holding_quantity") && (number < 0 || number != Math.rint(number)))) {
                        throw new IllegalArgumentException("Invalid price or quantity condition");
                    }
                }
                parsed.add(new Predicate(field, operator, value));
            }
            Double fraction = null;
            if (type == TriggerType.REDUCE) {
                fraction = number(row.get("reduce_fraction"));
                if (fraction <= 0 || fraction > 1) throw new IllegalArgumentException("reduce_fraction must be in (0,1]");
            }
            groups.add(new Group(id, List.copyOf(parsed), fraction));
        }
        return List.copyOf(groups);
    }

    public static boolean matches(Group group, Map<String, Object> snapshot) {
        for (Predicate p : group.all()) {
            if (!snapshot.containsKey(p.field()) || snapshot.get(p.field()) == null) return false;
            int comparison;
            try {
                comparison = p.field().equals("market_time")
                        ? LocalTime.parse(String.valueOf(snapshot.get(p.field()))).compareTo(LocalTime.parse(String.valueOf(p.value())))
                        : Double.compare(number(snapshot.get(p.field())), number(p.value()));
            } catch (IllegalArgumentException | java.time.DateTimeException ex) {
                return false;
            }
            boolean matched = switch (p.operator()) {
                case ">" -> comparison > 0;
                case ">=" -> comparison >= 0;
                case "<" -> comparison < 0;
                case "<=" -> comparison <= 0;
                case "==" -> comparison == 0;
                case "!=" -> comparison != 0;
                default -> false;
            };
            if (!matched) return false;
        }
        return true;
    }

    public static Double hardStop(Map<String, Object> payload) {
        return java.util.stream.Stream.concat(groups(payload, TriggerType.EXIT).stream(), groups(payload, TriggerType.INVALIDATION).stream())
                .filter(group -> group.all().size() == 1)
                .map(group -> group.all().get(0))
                .filter(p -> "current_price".equals(p.field()) && "<=".equals(p.operator()))
                .map(p -> number(p.value())).max(Double::compareTo).orElse(null);
    }

    public static double number(Object value) {
        if (!(value instanceof Number n) || !Double.isFinite(n.doubleValue())) {
            throw new IllegalArgumentException("A finite numeric value is required");
        }
        return n.doubleValue();
    }
}
