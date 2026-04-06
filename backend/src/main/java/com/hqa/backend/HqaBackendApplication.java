package com.hqa.backend;

import com.hqa.backend.config.HqaProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableConfigurationProperties(HqaProperties.class)
@EnableScheduling
public class HqaBackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(HqaBackendApplication.class, args);
    }
}
