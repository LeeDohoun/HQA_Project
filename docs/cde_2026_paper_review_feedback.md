# CDE 2026 논문 반영 피드백

작성일: 2026-06-19

## 검토 목적

현재 논문 초안이 프로젝트 산출물에 근거한 사실 주장인지, 그리고 CDE 제출용 논문으로 과장 없이 방어 가능한지 확인하였다. 검토는 로컬 코드와 실험 산출물, 그리고 다중 에이전트 리뷰 결과를 함께 기준으로 삼았다.

## 종합 평가

현재 논문의 핵심 방향은 프로젝트 사실과 대체로 일치한다. 특히 `as_of_date` 기반 시점 제한, Temporal RAG, 역할 분화형 Agentic AI, 결정론적/기술지표/LLM-only/hybrid 비교라는 큰 주장은 코드와 산출물로 뒷받침된다.

다만 제출용 논문에서는 성능 우위 주장을 더 엄격하게 제한해야 한다. hybrid multi-agent가 항상 우수한 전략이라는 주장은 근거가 부족하며, 논문의 강점은 수익률 자체보다 미래 정보 누수를 줄이는 검증 절차와 근거 추적 가능한 의사결정 구조에 있다.

## 반영해야 할 핵심 피드백

1. 2025년 구간은 순수 검증 구간이 아니라 tuning/reference 구간으로 명시해야 한다.
2. 2026Q1 long 결과는 1회 재조정과 3개 포지션 기반이므로 pilot 결과로만 해석해야 한다.
3. hybrid multi-agent가 결정론적 기준선 이상인 구간은 8개 중 4개이며, 모든 구간에서 우월하지 않다는 점을 유지해야 한다.
4. 최고 기술지표 기준선 이상인 구간은 2025 short, 2025 long, 2026Q1 short, 2026Q1 long의 4개로 정리해야 한다. 2023 short는 근접했지만 초과하지 못했다.
5. LLM-only가 일부 구간에서 hybrid보다 좋은 결과를 보였으므로, hybrid의 장점은 보편적 수익률 우위가 아니라 역할별 근거 분해와 위험 해석 가능성으로 제한해야 한다.
6. Table 2는 수익률 요약표이며, MDD와 Sharpe 등 위험 조정 지표는 보조 산출물에서 점검했다는 설명을 붙여야 한다.
7. Temporal RAG의 정보 누수 방지 주장은 코드 구조로 뒷받침되지만, 최종 제출 전 대표 `as_of_date`별 근거 감사 표를 보강하면 설득력이 높아진다.
8. 현재 보존된 요약 산출물은 논문 표를 재현하는 데 충분하지만, 일부 raw `result_json` 실행 파일은 현재 workspace에서 누락되어 있어 완전 재현 패키지로는 보완이 필요하다.

## 본문 반영 상태

아래 항목은 `docs/cde_2026_paper_draft_ko.md`에 반영하였다.

- 초록에서 2023, 2024, 2026Q1을 검증 구간으로, 2025를 튜닝 및 참조 구간으로 구분하였다.
- 실험 설계 절에 2025 tuning/reference 해석과 2026Q1 long pilot 해석을 추가하였다.
- Table 2 해석 문장에서 최고 기술지표 기준선 이상 구간을 정확히 정정하였다.
- LLM-only가 일부 구간에서 hybrid를 상회한다는 한계를 명시하였다.
- Table 2가 수익률 중심 요약이며 MDD/Sharpe는 보조 산출물에서 점검했다는 설명을 추가하였다.
- 결론에서 2025와 2026Q1 long의 해석 한계를 다시 명시하였다.

## 근거 파일

- 논문 초안: `docs/cde_2026_paper_draft_ko.md`
- DOCX 산출물: `docs/cde_2026_paper_draft_ko.docx`
- 비교 요약 산출물: `artifacts/paper_backtesting_exports/ai-strategy-comparison.json`
- 비교 보고서: `artifacts/paper_backtesting_exports/ai-strategy-comparison-report.md`
- Temporal RAG 구현: `backtesting/temporal_rag.py`
- 백테스트 구현: `backtesting/leader_backtest.py`
- DOCX 생성 스크립트: `scripts/build_cde_paper_docx.py`

## 제출 전 추가 권장 작업

- 저자 소속이 실제 제출 소속과 다르면 `독립 연구자 / Independent Researcher`를 교체한다.
- 대표 재조정일 1-2개에 대해 검색 문서의 `published_at <= as_of_date`를 보여주는 작은 감사 표를 추가한다.
- 가능하면 raw 실행 JSON을 함께 보존하여 요약 CSV/JSON에서 원본 실행 결과까지 이어지는 재현성을 강화한다.
- 제출 직전 CDE 공식 템플릿의 페이지 수, 글꼴, 캡션 위치를 Microsoft Word에서 육안 확인한다.
