package com.hqa.backend.config;

import static org.assertj.core.api.Assertions.*;

import java.time.LocalTime;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.bind.Bindable;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.context.properties.bind.PropertySourcesPlaceholdersResolver;
import org.springframework.boot.context.properties.source.ConfigurationPropertySources;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.MutablePropertySources;
import org.springframework.core.env.PropertySource;
import org.springframework.core.io.ClassPathResource;

/** application.yml binds the strategy settings, including the pf001 risk profile, with Spring's binder. */
class StrategyPropertiesBindingTest {

    private static StrategyProperties bindApplicationYml() throws Exception {
        List<PropertySource<?>> loaded = new YamlPropertySourceLoader().load("application", new ClassPathResource("application.yml"));
        MutablePropertySources sources = new MutablePropertySources();
        loaded.forEach(sources::addLast);
        Binder binder = new Binder(ConfigurationPropertySources.from(sources), new PropertySourcesPlaceholdersResolver(sources));
        return binder.bind("hqa.strategy", Bindable.ofInstance(new StrategyProperties())).get();
    }

    @Test
    void pf001UsesTerminationAndOtherStrategiesKeepTheDefaults() throws Exception {
        StrategyProperties properties = bindApplicationYml();
        StrategyProperties.Profile pf001 = properties.profile("pf001");
        assertThat(pf001.isDailyLossBuyBlock()).isFalse();
        assertThat(pf001.isDrawdownBuyBlock()).isFalse();
        assertThat(pf001.getTerminationDrawdownPct()).isEqualTo(25.0);
        StrategyProperties.Profile other = properties.profile("hf001_f_both");
        assertThat(other.isDailyLossBuyBlock()).isTrue();
        assertThat(other.isDrawdownBuyBlock()).isTrue();
        assertThat(other.getTerminationDrawdownPct()).isNull();
        assertThat(properties.isOrdersEnabled()).isFalse();
        assertThat(properties.getDailyLossLimitPct()).isEqualTo(2.0);
        assertThat(properties.getDrawdownStopPct()).isEqualTo(10.0);
    }

    @Test
    void continuousWindowsAndCloseRecordingHaveTheDocumentedDefaults() throws Exception {
        StrategyProperties properties = bindApplicationYml();
        assertThat(properties.contSellWindowTimes()).containsExactly(LocalTime.of(9, 5), LocalTime.of(9, 20));
        assertThat(properties.contBuyWindowTimes()).containsExactly(LocalTime.of(9, 20), LocalTime.of(10, 0));
        assertThat(properties.contSellCancelAtTime()).isEqualTo(LocalTime.of(9, 20));
        assertThat(properties.contBuyCancelAtTime()).isEqualTo(LocalTime.of(10, 0));
        assertThat(properties.navCloseRecordAtTime()).isEqualTo(LocalTime.of(15, 45));
        assertThat(properties.getNavCrosscheckTolerancePct()).isEqualTo(0.5);
        StrategyProperties fresh = new StrategyProperties();
        assertThat(fresh.contSellWindowTimes()).isEqualTo(properties.contSellWindowTimes());
        assertThatThrownBy(() -> new StrategyProperties.Profile().setTerminationDrawdownPct(0.0))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
