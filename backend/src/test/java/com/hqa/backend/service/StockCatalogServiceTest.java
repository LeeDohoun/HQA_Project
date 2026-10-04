package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

import com.hqa.backend.dto.ErrorCode;
import com.hqa.backend.entity.Stock;
import com.hqa.backend.exception.ApiException;
import com.hqa.backend.repository.StockRepository;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class StockCatalogServiceTest {
    private final StockRepository repository = mock(StockRepository.class);
    private final StockCatalogService service = new StockCatalogService(repository);

    @ParameterizedTest
    @ValueSource(strings = {"0015G0", "005930"})
    void acceptsAndSearchesExactKrxShortCodes(String code) {
        when(repository.findByCode(code)).thenReturn(Optional.of(new Stock(code, "종목", null, "KOSDAQ")));
        assertThatCode(() -> service.validateCode(code)).doesNotThrowAnyException();
        var response = service.search(code);
        assertThat(response.total()).isEqualTo(1);
        assertThat(response.results()).extracting(result -> result.code()).containsExactly(code);
        verify(repository, never()).searchByTerm(any(), any());
    }

    @ParameterizedTest
    @ValueSource(strings = {"0015g0", "15G0", "0015G0X", "0015-0"})
    void rejectsInvalidCodes(String code) {
        assertThatThrownBy(() -> service.validateCode(code)).isInstanceOf(ApiException.class)
                .extracting(e -> ((ApiException) e).getErrorCode()).isEqualTo(ErrorCode.STOCK_INVALID_CODE);
        verifyNoInteractions(repository);
    }

    @ParameterizedTest
    @ValueSource(strings = {"그린광학", "SAMSUN", "Samsung"})
    void preservesNameSearchIncludingSixUppercaseLetters(String term) {
        when(repository.searchByTerm(eq(term.toLowerCase(Locale.ROOT)), any()))
                .thenReturn(List.of(new Stock("0015G0", term, null, "KOSDAQ")));
        var response = service.search(term);
        assertThat(response.total()).isEqualTo(1);
        assertThat(response.results()).extracting(result -> result.code()).containsExactly("0015G0");
        verify(repository).searchByTerm(eq(term.toLowerCase(Locale.ROOT)), any());
    }
}
