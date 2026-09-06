package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class TradeConditionsTest {
    @Test
    void namedGroupRequiresEveryPredicateAndGroupsRemainAlternatives() {
        Map<String, Object> payload = Map.of("schema_version", 2, "entry_conditions", List.of(
                Map.of("id", "range", "all", List.of(predicate(">=", 100), predicate("<=", 110))),
                Map.of("id", "second", "all", List.of(predicate("==", 90)))));
        var groups = TradeConditions.groups(payload, TradeConditions.TriggerType.ENTRY);
        assertThat(TradeConditions.matches(groups.get(0), Map.of("current_price", 105))).isTrue();
        assertThat(TradeConditions.matches(groups.get(0), Map.of("current_price", 111))).isFalse();
        assertThat(TradeConditions.matches(groups.get(1), Map.of("current_price", 90))).isTrue();
    }

    @Test
    void legacyListKeepsExplicitOrSemanticsAndStableIds() {
        var groups = TradeConditions.groups(Map.of("entry_conditions", List.of(predicate(">", 100), predicate("<", 90))),
                TradeConditions.TriggerType.ENTRY);
        assertThat(groups).extracting(TradeConditions.Group::id).containsExactly("legacy-entry-0", "legacy-entry-1");
        assertThat(TradeConditions.matches(groups.get(1), Map.of("current_price", 85))).isTrue();
    }

    @Test
    void malformedNumbersAndUnsupportedFieldsCannotCreateConditions() {
        for (Object value : List.of("100", Double.NaN, Double.POSITIVE_INFINITY, true, 0)) {
            assertThatThrownBy(() -> TradeConditions.validate(Map.of("entry_conditions", List.of(predicate(">=", value)))))
                    .isInstanceOf(IllegalArgumentException.class);
        }
        assertThatThrownBy(() -> TradeConditions.validate(Map.of("entry_conditions", List.of(
                Map.of("field", "unknown", "operator", ">", "value", 10)))))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void emptyAndDuplicateGroupsAndUnknownVersionsAreRejected() {
        Map<String, Object> group = Map.of("id", "same", "all", List.of(predicate(">", 100)));
        assertThatThrownBy(() -> TradeConditions.validate(Map.of("schema_version", 2, "entry_conditions", List.of(group, group))))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> TradeConditions.validate(Map.of("schema_version", 2, "entry_conditions", List.of(Map.of("id", "x", "all", List.of())))))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> TradeConditions.validate(Map.of("schema_version", 3))).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void missingSnapshotValuesDoNotMatchAndReductionMustSpecifyFraction() {
        var group = TradeConditions.groups(Map.of("entry_conditions", List.of(predicate(">", 100))), TradeConditions.TriggerType.ENTRY).get(0);
        assertThat(TradeConditions.matches(group, Map.of())).isFalse();
        assertThatThrownBy(() -> TradeConditions.validate(Map.of("reduce_conditions", List.of(predicate(">", 100)))))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private static Map<String, Object> predicate(String operator, Object value) {
        return Map.of("field", "current_price", "operator", operator, "value", value);
    }
}
