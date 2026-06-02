package com.hqa.backend.dto;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import org.junit.jupiter.api.Test;

class BulkAnalysisRequestTest {

    private final ObjectMapper objectMapper = new ObjectMapper()
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);

    @Test
    void deserializesUiWatchlistItemsWithCamelCaseFields() throws Exception {
        BulkAnalysisRequest request = objectMapper.readValue(
                """
                {"items":[{"stockName":"삼성전자","stockCode":"005930"}]}
                """,
                BulkAnalysisRequest.class
        );

        assertThat(request.getItems()).hasSize(1);
        assertThat(request.getItems().get(0).getStockName()).isEqualTo("삼성전자");
        assertThat(request.getItems().get(0).getStockCode()).isEqualTo("005930");
    }

    @Test
    void deserializesUiWatchlistItemsWithSnakeCaseFields() throws Exception {
        BulkAnalysisRequest request = objectMapper.readValue(
                """
                {"items":[{"stock_name":"삼성전자","stock_code":"005930"}]}
                """,
                BulkAnalysisRequest.class
        );

        assertThat(request.getItems()).hasSize(1);
        assertThat(request.getItems().get(0).getStockName()).isEqualTo("삼성전자");
        assertThat(request.getItems().get(0).getStockCode()).isEqualTo("005930");
    }
}
