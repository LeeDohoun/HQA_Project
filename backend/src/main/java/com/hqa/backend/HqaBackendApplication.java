package com.hqa.backend;

import com.hqa.backend.config.HqaProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties(HqaProperties.class)
public class HqaBackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(HqaBackendApplication.class, args);
    }
}
