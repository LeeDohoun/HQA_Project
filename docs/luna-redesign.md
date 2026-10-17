# HQA 워크스페이스 개편

## 화면

아이보리·잉크·라임을 중심으로 랜딩을 다시 구성했다. CSS로 만든 링 오브젝트와 세 전문가 소개를 사용하며, 실제 사용자 근거가 없는 기존 후기와 현재 엔진의 성과로 오해할 수 있는 랜딩 표현은 제거했다.

- 랜딩: 서비스 소개 → 세 전문가 → 워치리스트/분석/PAPER 흐름 → 시작.
- 대시보드: 데스크톱 사이드 내비게이션, 모바일 가로 메뉴, 자산·AI 활동·주문 카드.
- 로그인·회원가입·종목 상세·분석 보고서·온보딩·계좌 설정·백테스트·FAQ: 공통 토큰 적용.
- 온보딩과 계좌 설정: PAPER 환경만 선택. 기존 실계좌 등록 상태는 사실대로 표시하되 새 폼은 모의 계좌 설정으로 시작.
- FAQ: 현재 Analyst·Quant·Chartist 공통 분석과 계좌별 RiskManager 판단을 구분. 과거 연구 결과와 현재 서비스 성과를 구분.
- 접근성: 키보드 포커스, 랜딩 본문 바로가기, 현재 메뉴 표시, 동작 줄이기, 로그인 자동완성 및 오류 안내.

`frontend/src/styles/luna.css`가 제품 공통 토큰과 새 레이아웃을 담당한다. 기존 대시보드·종목 화면의 CSS는 각각 `workspace.css`, `stock.css`로 분리하고 루트 아래에 범위를 제한했다. 화면 이동 시 `.ed-*` 선택자가 서로 덮어쓰는 문제를 줄였다.

## FE → BE → AI 계약

| 화면/기능 | BE | AI 또는 데이터 소스 |
| --- | --- | --- |
| 사용자/성향/계좌 | `/api/v1/auth/*` | Spring 세션·DB·KIS 검증 |
| 관심 종목 | `/api/v1/watchlist` | 사용자별 DB |
| 종목 분석 | `POST /api/v1/analysis`, `/bulk` | `/runtime/stock-preview`; full, 재시도 0 |
| 작업 조회 | `/api/v1/analysis/{id}`, `/{id}/progress` | `/runtime/tasks/{id}` + 사용자 소유권 및 저장 결과 |
| 분석 이력 | `/api/v1/analysis/history/list` | 사용자별 DB |
| 뉴스/공시 | `/api/v1/stocks/{code}/news`, `/disclosures` | AI 서버의 동일한 종목 피드 |
| 자동매매 상태 | `/api/v1/trading/status` | DB 활성 상태 + AI `/health` 확인 |
| 자동매매 설정 | `/api/v1/trading/auto` | 계좌별 PAPER 설정 |
| 주문/근거 | `/api/v1/trading/orders`, `/explanations` | 계좌별 기록과 Spring 주문 관리 |

이전 `/api/v1/trading/decision/preview`, `/decision/execute`는 현재 AI 서버에 대응 경로가 없어 인증 후 **410 Gone**을 반환한다. OpenAPI에도 종료된 경로임을 표시했다. 수동 종목 분석이 주문을 실행하도록 대체하지 않았다.

## BE 개선 반영

- AI GET 실패를 빈 성공 목록으로 바꾸지 않는다. HTTP 실패·JSON 손상은 502, 연결 실패와 AI 피드의 오류 응답은 503으로 전달한다.
- 잘못된 AI 작업 ID는 사용자 입력 오류인 400 대신 upstream 오류인 502를 반환하고 저장하지 않는다.
- 뉴스·공시 종목 코드를 6자리 숫자로 검증한다.
- upstream 응답 본문과 내부 URL을 공개 오류에서 제거했다.
- 잘못된 JSON/쿼리 타입을 400으로 처리한다. 예상하지 못한 서버 예외의 내부 메시지는 클라이언트에 노출하지 않는다.
- `/health`의 기존 `langgraphAvailable` 필드는 호환성을 유지하면서 실제 AI HTTP 가용성을 반영한다. 이것이 모델 키, 수집 데이터, 주문 준비 상태 전체를 보장하지는 않는다.
- 상세 헬스에 AI 상태를 포함하고 Redis 연결을 닫는다.

## 프록시와 요청 처리

기본 `NEXT_PUBLIC_API_BASE`는 빈 문자열이다. 브라우저는 `/api`로 요청하며 Next.js가 `BACKEND_PROXY_TARGET`으로 전달한다. Docker는 `http://backend:8000`, 개발 스크립트는 선택된 BE 포트를 사용한다. 기존 환경변수에 명시한 `NEXT_PUBLIC_API_BASE`는 계속 우선하므로 동일 출처를 쓰려면 그 값을 비워야 한다.

FE 클라이언트는 204, 중첩 오류, 잘못된 JSON, 네트워크 오류, 취소, 45초 제한을 처리한다. 분석·주문은 자동 재전송하지 않는다. 응답 제한 시간이 지나도 서버 작업이 완료됐을 수 있으므로 이력을 확인하도록 안내한다.

## 검증

- `npm --prefix frontend run build`
- `npm --prefix frontend test`: 프록시 기본값, 세션, 204, 중첩 오류, HTML 오류 차단, JSON 파싱, 취소와 쓰기 재전송 방지.
- `mvn -f backend/pom.xml test`: 기존 회귀 테스트와 AI 응답·헬스·잘못된 요청·종료 경로 테스트.
- 브라우저: 데스크톱/390px 모바일 랜딩, 대시보드, 종목 선택 및 실행 전 확인/취소, 검색, 로그인, 회원가입을 확인.

화면 검증에는 `node frontend/tests/preview-api.mjs`를 사용했다. 이 서버는 127.0.0.1:8000에만 바인딩하고 메모리의 고정 GET 응답만 반환한다. 쓰기 요청은 503이며 자격 증명, DB, LLM, KIS를 사용하지 않는다. 실제 백엔드와 동시에 실행할 수 없다. 검증 후 종료했다.

실제 PostgreSQL·Redis·AI·KIS를 함께 켜는 통합 운용과 유료 LLM/주문은 이번 검증에 포함하지 않았다.

## 후속으로 권장하는 BE 작업

1. **분석 제출의 멱등성과 복구**: 현재 AI가 작업을 접수한 후 DB 저장이 실패하면 추적되지 않는 작업이 남을 수 있다. 요청 ID와 outbox/재조정 흐름을 FE·BE·AI 전체에 걸쳐 설계할 필요가 있다. 단순 자동 재시도는 비용 중복을 만들 수 있어 이번에 추가하지 않았다.
2. **계약 자동 검증**: 공개 OpenAPI에서 FE 타입을 생성하고 실제 FastAPI 응답과 Spring 어댑터 간 계약 테스트를 CI에서 실행하면 과거 경로 재사용을 예방할 수 있다.
3. **운영 준비 상태 분리**: 현재 HTTP 가용성과 데이터 최신성·모델 예산·계좌 주문 가능 상태를 별도 지표로 나누고 캐시된 관측값을 제공하는 것이 좋다.
