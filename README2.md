# HQA `ai-data-main` 통합 브랜치 정리

> `ai-data-main`은 기존 `ai-main`의 에이전트 중심 분석 시스템과 `rag-data-pipeline`의 데이터 수집/적재/리빌드 계층을 하나의 코드베이스로 묶은 통합 브랜치입니다.

---

## 1. 이 브랜치의 목적

기존 브랜치 역할은 명확히 분리되어 있었습니다.

- `ai-main`
  - 멀티 에이전트 분석
  - LangGraph 워크플로우
  - Risk Manager 기반 최종 투자 판단
  - AI 서버, CLI, 자율 실행기
  - ChromaDB + BM25 + Reranker 기반 agent-side RAG

- `rag-data-pipeline`
  - 뉴스/공시/포럼/차트 데이터 수집
  - raw 저장
  - corpus / market_data / BM25 / vector store 빌드
  - theme 중심 데이터 파이프라인

`ai-data-main`은 이 둘을 아래 방식으로 연결합니다.

1. `ai-main`의 에이전트 계층은 유지
2. `rag-data-pipeline`의 ingestion / build 계층을 선택 반입
3. 두 계층 사이에 어댑터를 넣어 런타임 연결

핵심 원칙은 다음과 같습니다.

- 에이전트 계층은 `ai-main`이 계속 소유
- 데이터 producer 계층은 `rag-data-pipeline`이 제공
- 통합은 “코드 덮어쓰기”가 아니라 “계층 결합 + 호환 어댑터” 방식으로 수행

---

## 2. 무엇이 어디서 왔는가

### `ai-main`에서 유지된 것

아래 계층은 기본적으로 `ai-main` 쪽 구현을 유지합니다.

- `src/agents/`
- `src/tools/`
- `src/runner/`
- `src/tracing/`
- `src/utils/`
- `ai_server/`
- `main.py`
- `prompts/`

즉, 실제 사용자 질의 처리와 최종 투자 판단은 여전히 `ai-main` 구조를 따릅니다.

### `rag-data-pipeline`에서 반입된 것

이번 통합에서 새로 들어온 계층은 다음입니다.

- [src/ingestion](/home/are01/HQA_Project/HQA_Project/src/ingestion)
  - source collector
  - raw 적재 오케스트레이션
  - KIS chart / Naver news / forum / DART 수집

- [src/data_pipeline](/home/are01/HQA_Project/HQA_Project/src/data_pipeline)
  - collector compatibility surface
  - corpus builder
  - agent 계층에서 쓸 `PriceLoader`

- [src/retrieval](/home/are01/HQA_Project/HQA_Project/src/retrieval)
  - 경량 hybrid retrieval entry point
  - JSON vector store + BM25 조합

- `src/rag/` 일부 보강
  - [dedupe.py](/home/are01/HQA_Project/HQA_Project/src/rag/dedupe.py)
  - [source_registry.py](/home/are01/HQA_Project/HQA_Project/src/rag/source_registry.py)
  - [raw_layer2_builder.py](/home/are01/HQA_Project/HQA_Project/src/rag/raw_layer2_builder.py)
  - [vector_store.py](/home/are01/HQA_Project/HQA_Project/src/rag/vector_store.py) 확장

---

## 3. 구조적으로 어떻게 합쳐졌는가

### 통합 전

```text
ai-main
├── agents
├── tools
├── runner
└── full RAG for agents

rag-data-pipeline
├── ingestion
├── data_pipeline
├── retrieval
└── raw -> corpus -> market_data -> vector/bm25 build
```

### 통합 후

```text
ai-data-main
├── agents                 # ai-main 유지
├── tools                  # ai-main 유지
├── runner                 # ai-main 유지
├── tracing/utils          # ai-main 유지
├── ingestion              # rag-data-pipeline 반입
├── data_pipeline          # rag-data-pipeline 반입 + ai-main 어댑터
├── retrieval              # rag-data-pipeline 반입
└── rag
    ├── agent RAG stack    # ai-main 유지
    ├── dedupe/source map  # rag-data-pipeline 반입
    ├── layer2 builder     # rag-data-pipeline 반입 + agent sync 추가
    └── lightweight store  # rag-data-pipeline 경량 JSON vector store 공존
```

요약하면,

- `ai-main`의 online 분석 경로는 살아 있고
- `rag-data-pipeline`의 offline build 경로가 추가됐고
- `RawLayer2Builder`가 둘을 이어주는 브리지 역할을 합니다

---

## 4. 핵심 통합 포인트

### 4.1 `PriceLoader` 추가

`ai-main`의 [charts_tools.py](/home/are01/HQA_Project/HQA_Project/src/tools/charts_tools.py)는 원래 `src.data_pipeline.price_loader.PriceLoader`를 기대했지만 구현이 없었습니다.

그래서 [src/data_pipeline/price_loader.py](/home/are01/HQA_Project/HQA_Project/src/data_pipeline/price_loader.py)를 새로 추가했습니다.

이 로더는 다음 산출물을 읽습니다.

- `data/market_data/<theme>/chart.jsonl`
- `data/market_data/<theme>/combined.jsonl`
- `data/raw/chart/<theme>.jsonl`

그리고 이를 `Chartist`가 기대하는 `pandas.DataFrame` 형태로 변환합니다.

즉, 데이터 파이프라인이 만든 시장 데이터가 이제 기술적 분석 에이전트로 직접 연결됩니다.

### 4.2 `RawLayer2Builder` -> agent RAG 동기화

기존 `rag-data-pipeline`의 `RawLayer2Builder`는 주로 아래를 생성합니다.

- `data/corpora/...`
- `data/market_data/...`
- `data/vector_stores/...`
- `data/bm25/...`

통합 브랜치에서는 [src/rag/raw_layer2_builder.py](/home/are01/HQA_Project/HQA_Project/src/rag/raw_layer2_builder.py)에 agent-side 동기화를 추가했습니다.

즉, layer2 build를 돌리면 선택적으로:

1. raw -> corpus / market_data / JSON vector / BM25 생성
2. 동시에 `ai-main`이 사용하는 Chroma + BM25 경로에도 반영

이렇게 되므로 offline pipeline과 online agent 검색 경로가 분리되지 않습니다.

### 4.3 경량 retrieval 계층 공존

`ai-main`의 기본 검색은 [src/rag/retriever.py](/home/are01/HQA_Project/HQA_Project/src/rag/retriever.py) 중심입니다.

이번 통합에서는 `rag-data-pipeline` 쪽 경량 retrieval도 같이 들어왔습니다.

- [src/retrieval/services.py](/home/are01/HQA_Project/HQA_Project/src/retrieval/services.py)

이 계층은:

- JSON vector store
- BM25
- RRF merge

를 이용해 비교적 가벼운 검색을 수행합니다.

즉, 현재 브랜치에는 retrieval이 2종류 있습니다.

1. agent용 full RAG
2. pipeline/inspection용 lightweight retrieval

이 둘은 목적이 다릅니다.

### 4.4 `src/rag/vector_store.py` 확장

원래 `ai-main`의 `vector_store.py`는 Chroma 기반이었습니다.

여기에 다음이 추가되었습니다.

- `SimpleVectorStore`
- `SourceRAGBuilder`

이 추가 덕분에 `rag-data-pipeline` 스타일의 source별 JSON vector store도 같은 `src/rag` 패키지에서 처리할 수 있습니다.

즉, 한 파일 안에 2개의 저장소 계층이 공존합니다.

- 고성능 online store: Chroma
- 경량 offline store: JSON vector store

---

## 5. 현재 계층별 역할

### Layer A. Data Producer

수집과 raw 적재를 담당합니다.

- [src/ingestion/services.py](/home/are01/HQA_Project/HQA_Project/src/ingestion/services.py)
- [src/ingestion/types.py](/home/are01/HQA_Project/HQA_Project/src/ingestion/types.py)
- [src/ingestion/naver_news.py](/home/are01/HQA_Project/HQA_Project/src/ingestion/naver_news.py)
- [src/ingestion/dart.py](/home/are01/HQA_Project/HQA_Project/src/ingestion/dart.py)
- [src/ingestion/naver_forum.py](/home/are01/HQA_Project/HQA_Project/src/ingestion/naver_forum.py)
- [src/ingestion/kis_chart.py](/home/are01/HQA_Project/HQA_Project/src/ingestion/kis_chart.py)

산출물:

- `data/raw/news/*.jsonl`
- `data/raw/dart/*.jsonl`
- `data/raw/forum/*.jsonl`
- `data/raw/chart/*.jsonl`

### Layer B. Data Build / Layer2

수집된 raw 데이터를 검색 및 분석 가능한 자산으로 변환합니다.

- [src/data_pipeline/rag_builder.py](/home/are01/HQA_Project/HQA_Project/src/data_pipeline/rag_builder.py)
- [src/rag/raw_layer2_builder.py](/home/are01/HQA_Project/HQA_Project/src/rag/raw_layer2_builder.py)
- [src/rag/source_registry.py](/home/are01/HQA_Project/HQA_Project/src/rag/source_registry.py)
- [src/rag/dedupe.py](/home/are01/HQA_Project/HQA_Project/src/rag/dedupe.py)

산출물:

- `data/corpora/...`
- `data/market_data/...`
- `data/vector_stores/...`
- `data/bm25/...`

### Layer C. Agent Consumer

실제 분석 에이전트가 데이터를 읽어 투자 판단을 만듭니다.

- [src/agents/analyst.py](/home/are01/HQA_Project/HQA_Project/src/agents/analyst.py)
- [src/agents/quant.py](/home/are01/HQA_Project/HQA_Project/src/agents/quant.py)
- [src/agents/chartist.py](/home/are01/HQA_Project/HQA_Project/src/agents/chartist.py)
- [src/agents/risk_manager.py](/home/are01/HQA_Project/HQA_Project/src/agents/risk_manager.py)
- [src/tools/rag_tool.py](/home/are01/HQA_Project/HQA_Project/src/tools/rag_tool.py)
- [src/data_pipeline/price_loader.py](/home/are01/HQA_Project/HQA_Project/src/data_pipeline/price_loader.py)

---

## 6. 실제 데이터 흐름

### 전체 흐름

```text
CollectRequest
  -> IngestionService
  -> raw JSONL 저장
  -> RawLayer2Builder
     -> corpora 생성
     -> market_data 생성
     -> JSON vector store 생성
     -> BM25 생성
     -> agent RAG(Chroma/BM25) 동기화
  -> Supervisor / Analyst / Chartist / Risk Manager
```

### 문서형 데이터 흐름

```text
news / dart / forum
 -> raw
 -> chunking
 -> dedupe
 -> corpora
 -> vector / bm25
 -> Analyst 검색 컨텍스트
```

### 시장 데이터 흐름

```text
chart / quote 계열
 -> raw
 -> market_data
 -> PriceLoader
 -> TechnicalAnalyzer
 -> ChartistAgent
```

---

## 7. 어떤 파일이 실제 연결점인가

연결에서 중요한 파일은 아래입니다.

- [src/ingestion/services.py](/home/are01/HQA_Project/HQA_Project/src/ingestion/services.py)
  - raw 수집 오케스트레이터

- [src/rag/raw_layer2_builder.py](/home/are01/HQA_Project/HQA_Project/src/rag/raw_layer2_builder.py)
  - pipeline 산출물 생성
  - agent-side RAG 동기화

- [src/data_pipeline/price_loader.py](/home/are01/HQA_Project/HQA_Project/src/data_pipeline/price_loader.py)
  - `Chartist`가 실제로 읽는 시장 데이터 어댑터

- [src/rag/vector_store.py](/home/are01/HQA_Project/HQA_Project/src/rag/vector_store.py)
  - Chroma 기반 vector store
  - 경량 JSON vector store 공존

- [src/retrieval/services.py](/home/are01/HQA_Project/HQA_Project/src/retrieval/services.py)
  - lightweight retrieval 경로

---

## 8. 왜 이런 방식으로 통합했는가

두 브랜치를 통째로 merge하지 않은 이유는 다음과 같습니다.

1. `src/rag`의 책임이 달랐습니다.
   - `ai-main`: agent-side retrieval
   - `rag-data-pipeline`: build/retrieval asset 생성

2. `ai-main`은 서비스/에이전트 중심이고,
   `rag-data-pipeline`은 batch/pipeline 중심입니다.

3. 코드 소유권을 유지하지 않으면 이후 유지보수가 어려워집니다.

그래서 통합 전략은 아래였습니다.

- 서비스 계층은 `ai-main` 유지
- producer 계층만 선택 반입
- 연결은 adapter / sync 포인트로 처리

이 방식이 충돌을 가장 적게 만들고, 이후에도 역할 분리를 유지하기 쉽습니다.

---

## 9. 현재 제약 사항

이 브랜치는 구조적으로 통합되었지만, 실행 환경에는 몇 가지 전제가 있습니다.

### 필수/권장 의존성

- `pandas`
  - `PriceLoader` + `Chartist` 기술적 분석에 필요

- `rank_bm25`
  - BM25 사용 시 필요
  - 없으면 BM25는 비활성화되고 경고만 출력됩니다

- full agent RAG 관련 의존성
  - Chroma / LangChain / OCR / embedding / reranker 계열

### 현재 상태

- 경량 retrieval import는 heavy OCR 의존성 없이 가능하도록 정리했습니다
- `RawLayer2Builder` import도 BM25 미설치 상태에서 로드는 가능하게 정리했습니다
- full sync 실행은 기존 agent RAG 의존성이 있어야 정상 동작합니다

즉:

- 구조 통합은 완료
- 최소 import 경량화는 완료
- 실제 full pipeline 운영은 의존성 설치가 필요

---

## 10. 이 브랜치에서 추천하는 운영 방식

### 권장 사용 순서

1. ingestion 실행
2. raw layer2 build 실행
3. agent-side RAG sync 확인
4. Supervisor / CLI / AI server 분석 실행

### 역할 분리 관점

- `src/ingestion`, `src/data_pipeline`, `src/retrieval`
  - batch / offline / build 중심

- `src/agents`, `src/tools`, `src/runner`
  - online / inference / decision 중심

즉, 이 브랜치는 “하나의 코드베이스”이지만, 여전히 내부적으로는 producer-consumer 구조를 유지합니다.

---

## 11. 브랜치별 비교 요약

| 항목 | ai-main | rag-data-pipeline | ai-data-main |
|------|---------|-------------------|--------------|
| 멀티 에이전트 | 있음 | 없음 | 있음 |
| LangGraph | 있음 | 없음 | 있음 |
| AI 서버 | 있음 | 없음 | 있음 |
| 자율 매매 | 있음 | 없음 | 있음 |
| 데이터 수집기 | 제한적 | 있음 | 있음 |
| raw 저장 | 제한적 | 있음 | 있음 |
| corpus build | 제한적 | 있음 | 있음 |
| market_data build | 없음/미완 | 있음 | 있음 |
| PriceLoader | 기대만 존재 | 없음 | 있음 |
| lightweight retrieval | 없음 | 있음 | 있음 |
| agent RAG sync | 있음 | 없음 | 있음 |

---

## 12. 이번 통합에서 실제 추가/변경된 핵심 파일

### 새로 들어온 디렉토리

- [src/ingestion](/home/are01/HQA_Project/HQA_Project/src/ingestion)
- [src/data_pipeline](/home/are01/HQA_Project/HQA_Project/src/data_pipeline)
- [src/retrieval](/home/are01/HQA_Project/HQA_Project/src/retrieval)

### 기존 파일 보강

- [src/rag/__init__.py](/home/are01/HQA_Project/HQA_Project/src/rag/__init__.py)
- [src/rag/bm25_index.py](/home/are01/HQA_Project/HQA_Project/src/rag/bm25_index.py)
- [src/rag/vector_store.py](/home/are01/HQA_Project/HQA_Project/src/rag/vector_store.py)
- [src/rag/raw_layer2_builder.py](/home/are01/HQA_Project/HQA_Project/src/rag/raw_layer2_builder.py)

---

## 13. 결론

`ai-data-main`은 단순한 merge 브랜치가 아닙니다.

이 브랜치는:

- `ai-main`의 분석/판단 시스템을 유지하면서
- `rag-data-pipeline`의 수집/리빌드 체계를 반입하고
- 두 계층을 `PriceLoader`, `RawLayer2Builder`, `SimpleVectorStore`, agent RAG sync로 연결한

실질적인 통합 운영 브랜치입니다.

한 줄로 요약하면:

> `ai-data-main`은 “데이터를 직접 만들 수 있고, 그 데이터를 바로 에이전트가 소비할 수 있는” HQA 통합 실행 브랜치입니다.
