package com.hqa.backend.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Customizes the auto-generated OpenAPI document served by springdoc.
 * Spec JSON: /v3/api-docs   Swagger UI: /swagger-ui.html
 */
@Configuration
public class OpenApiConfig {

    private final HqaProperties properties;

    public OpenApiConfig(HqaProperties properties) {
        this.properties = properties;
    }

    @Bean
    public OpenAPI hqaOpenApi() {
        return new OpenAPI().info(new Info()
                .title("HQA Backend API")
                .description("REST API for the HQA stock analysis & trading platform.")
                .version(properties.getAppVersion()));
    }
}
