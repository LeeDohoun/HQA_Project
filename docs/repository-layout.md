# 저장소 구조와 명령 이전

## 구분 기준

- 서비스: `frontend/`, `backend/`, `ai_server/`.
- Python 구현: `src/`. `runner/shared_analysis.py`가 현재 공통 분석 경로이며, 기존 에이전트·검색 API는 대화와 과거 연구에서 참조하므로 함께 유지합니다.
- 수집 명령: `scripts/data/`. 수집과 분석을 한꺼번에 실행하는 legacy 명령은 제거했습니다.
- 평가 명령: `python -m backtesting`. 과거 전략 검증과 현재 PAPER 기록 평가는 구분된 하위 명령입니다.
- 현재 데이터: `HQA_DATA_DIR` 아래. 기본값은 `data/`이며 원천 이력·재사용 캐시·주문·예산 상태가 있습니다.
- 과거 연구: `research/`. 기존 결과를 이동했으며 원본 바이트와 이전 경로는 manifest로 검증합니다.
- 웹 배포 자료: `frontend/public/backtesting/`. 웹 화면의 실제 입력이므로 연구 원본과 별도로 유지합니다.

## 수집 명령

프로젝트 루트에서 `python -m ...` 형식으로 실행합니다.

| 이전 파일 | 현재 명령 |
| --- | --- |
| `scripts/run_pipeline.py --collect-and-build` | `python -m scripts.data.collect` |
| `scripts/theme_pipeline.py` | `python -m scripts.data.collect` |
| `scripts/build_evidence_index.py` | `python -m scripts.data.build` |
| `scripts/run_theme_batch.py` | `python -m scripts.data.batch` |
| `scripts/collect_themes_loop.py` | `python -m scripts.data.loop` |
| `scripts/collect_all_naver_themes.py` | `python -m scripts.data.discover` |
| `scripts/download_dart_corp_codes.py` | `python -m scripts.data.corp_codes` |
| `scripts/collect_market_context.py` | `python -m scripts.data.market_context` |

수집기는 기본적으로 수집 후 빌드까지 수행하며 LLM과 주문 코드를 호출하지 않습니다. 이전 `--mode`는 `--update-mode`로 지정합니다. `--full`, `--build-and-analyze`, `--analyze-only`는 더 이상 제공하지 않습니다. 정기 작업의 기존 경로도 위 명령으로 변경해야 합니다. 이 정리는 cron이나 실행 중인 프로세스를 변경하지 않습니다.

기존 `run_pipeline.py`의 `reports/<theme>_pipeline_report.json`은 더 이상 갱신하지 않습니다. 현재 수집 결과는 `reports/<theme>_ingestion_report.json`이며, 중첩된 `steps` 대신 최상위 `status`, `build_status`, `per_stock_reports`를 확인합니다. 이전 보고서를 읽던 외부 작업도 파일명과 구조를 함께 바꿔야 합니다. 기존 보고서 자체는 삭제하지 않았습니다.

종목 목록은 기본 재사용하며 `--refresh-targets`로 다시 조회합니다. `--save-only`는 종목 목록만 저장하고 원문·가격 수집과 빌드는 생략합니다. `discover`는 전체 테마의 종목 목록을 수집하는 별도 명령입니다. 배치와 단일 수집은 동일한 기본 소스·증분 날짜 규칙을 사용합니다.

## 평가 명령

`backtesting/`의 기존 엔진 모듈 import는 유지합니다. 표준 실행 창구는 `python -m backtesting --help`입니다. `scripts/evaluate_paper_runtime.py`와 `scripts/evaluate_paper_performance.py`는 각각 `python -m backtesting paper-runtime`, `python -m backtesting paper-performance`로 이동했습니다.

새 백테스트 출력과 재사용 LLM 캐시의 기본 위치는 `data/backtest_results/`입니다. 기존 실험 결과는 `research/backtesting/results/`에 보관되어 예전 작업 ID의 운영 결과 조회 경로와 구분됩니다. 이력 안의 원래 경로 문자열은 연구 기록이므로 바꾸지 않았습니다. 현재 Luna 분석 입력의 관측 시점 계약이 과거 엔진 전체에 적용되었다고 가정하면 안 됩니다.

## 제거한 부분

- 사용하지 않는 `src/database/` raw PostgreSQL 어댑터. 실제 계좌 DB는 `backend/`와 Flyway에서 관리합니다.
- 중복된 루트 `Dockerfile`. Compose가 사용하는 `ai_server/Dockerfile`을 기준으로 유지합니다.
- `main.py`와 Supervisor 내부의 이미 반환 후 도달할 수 없었던 분석 코드. 현재 대화 API와 분석 서버 요청 경로는 보존합니다.
- 연결되지 않은 `/prototype` 샘플 화면. 실제 `/backtesting/ai` 화면과 배포 자료는 보존합니다.
- 과거 `docs/superpowers/plans/` 작업 계획. 현재 운영 문서와 구분합니다.

`python main.py`는 인자가 없으면 도움말만 표시합니다. 제거된 `--stock`, `--quick`, `--theme`, `--auto`, `--loop`, `--paper`, `--dry-run` 옵션은 오류로 거부합니다. 현재 지원하는 시세 조회·분석 서버 요청·저장 보고서 preview는 `python main.py --help`에서 확인하세요.

## 다른 컴퓨터로 수집 이전

동일 코드 버전과 Python 의존성을 설치하고 수집에 필요한 DART/KRX 키만 설정합니다. 수집 담당은 하나로 두며 파일 잠금·캐시는 여러 컴퓨터 사이의 중복 요청을 막지 않습니다.

수집을 이어갈 때는 기존 `raw/`와 `collection_state/`를 함께 옮겨 관측 이력과 증분 범위를 보존합니다. 분석 서버는 `raw/`의 종목 목록·재무, `canonical_index/`, `market_data/`, 수집한 경우 `market_context/`가 필요합니다. 수집을 마친 스냅샷을 임시 경로로 전송하고, 참조된 generation 파일이 모두 있는지 검증한 후 분석을 일시 정지한 상태에서 반영하세요. 자동 동기화는 구현되어 있지 않습니다.

`.env`, 계좌·주문 DB, LLM 예산 원장, 개인별 audit를 통째로 복사하거나 초기화하지 마세요. 기존 generation은 진행 중인 분석이 참조할 수 있으므로 임의 삭제하지 않습니다.
