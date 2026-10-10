package com.hqa.backend.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class HqaPropertiesTest {

    @Test
    void internalTokenIgnoresSurroundingWhitespaceFromEnvFiles() {
        HqaProperties properties = new HqaProperties();
        properties.setInternalToken(" shared-token\r\n");
        assertThat(properties.getInternalToken()).isEqualTo("shared-token");
        properties.setInternalToken(null);
        assertThat(properties.getInternalToken()).isEmpty();
    }

    @Test
    void aiServerUrlDropsTrailingSlashesSoPathsDoNotDoubleThem() {
        HqaProperties properties = new HqaProperties();
        properties.setAiServerUrl(" http://ai:8001// ");
        assertThat(properties.getAiServerUrl() + "/runtime/stock-preview").isEqualTo("http://ai:8001/runtime/stock-preview");
    }
}
