# 시점 제한 Temporal RAG와 Agentic AI를 활용한 동적 산업 테마 후보군 검증 프레임워크

**English Title:** A Point-in-Time Temporal RAG Validation Framework for Agentic Industrial Theme Candidate Prioritization

**Author:** 이도훈  
**Affiliation:** 광운대학교  
**English Author:** Dohoun LEE  
**English Affiliation:** Kwangwoon University

## ABSTRACT

동적 산업 테마의 후보 기업 평가는 뉴스, 공시, 커뮤니티 반응, 가격 시계열처럼 생성 시점과 신뢰도가 다른 근거를 함께 다루어야 한다. 특히 과거 시점의 의사결정을 재현할 때는 미래에 공개된 문서나 가격 특징이 평가에 섞이는 정보 누수 문제가 발생할 수 있다. 본 연구는 AI 산업 테마를 대상으로 의사결정 시점 이전의 근거만 검색하는 Temporal RAG와 역할 분화 Agentic AI 평가 구조를 결합한 검증 프레임워크를 제안한다. 제안 방법은 Analyst, Quant, Chartist, Risk Manager 관점에서 후보군을 평가하되, 가격 기반 Chartist는 규칙 및 수식 특징을 포함하는 hybrid multi-perspective 구조로 설계하였다. 실험은 2023년, 2024년, 2026년 1분기를 검증 구간으로, 2025년을 튜닝 및 참조 구간으로 구분하고, 5거래일 및 60거래일 보유 조건에서 결정론적 기준선, 기술지표 기준선, LLM-only, hybrid multi-agent를 비교하였다. 결과적으로 hybrid multi-agent는 8개 기간-구간 조합 중 결정론적 기준선 이상 4건, 최고 기술지표 기준선 이상 4건을 보였으나 모든 구간에서 일관된 우월성을 보이지는 않았다. 따라서 본 연구의 핵심 기여는 수익률 보장이 아니라 시점 제한 근거 검색, 검증 재현성, 그리고 근거 추적 가능한 의사결정 지원 구조에 있다.

**Key Words:** Agentic AI, Decision support, Industry theme, Temporal RAG, Validation framework

## 1. 서론

산업 테마 기반 후보군 평가는 단일 정형 지표만으로 설명하기 어렵다. 한 기업이 특정 테마의 선도 후보로 부상하는 과정에는 공시, 뉴스, 제품 및 수주 이슈, 온라인 반응, 가격 및 거래량 변화가 비동기적으로 결합된다. 이러한 문제는 CDE 관점에서 보면 복수의 이질 데이터와 지능형 의사결정 모델을 연결하는 모델링 및 검증 문제로 볼 수 있다.

그러나 과거 성과 검증을 단순 백테스트로 수행하면 평가 시점 이후에 공개된 문서나 가격 특징이 후보 선정에 섞이는 문제가 발생한다. 이는 의사결정 모델의 실제 성능보다 낙관적인 결과를 만들 수 있으며, 백테스트 과적합과 정보 누수의 주요 원인이 된다[3]. 특히 LLM 또는 RAG 기반 시스템에서는 검색 코퍼스의 시간 경계가 명확하지 않을 때 위험이 커진다[1]. 따라서 동적 산업 테마 후보군 검증에는 특정 `as_of_date` 기준으로 이용 가능한 근거만 사용하고, 후보 선정 이후의 보유기간 수익률은 별도 평가 단계에서만 사용하는 절차가 필요하다.

본 연구는 AI 산업 테마를 대상으로 시점 제한 Temporal RAG와 역할 분화 Agentic AI 평가 구조를 결합한 검증 프레임워크를 제안한다. 연구의 기여는 다음과 같다. 첫째, 문서와 가격 특징을 의사결정 시점 이전 데이터로 제한하는 point-in-time 검증 절차를 구성하였다. 둘째, Analyst, Quant, Chartist, Risk Manager 역할을 분리하여 후보 평가 근거를 추적 가능하게 만들었다. 셋째, 결정론적 기준선과 기술지표 기준선을 함께 제시하여 hybrid multi-agent 결과가 특정 구간에서 경쟁적이지만 항상 우월하지는 않다는 점을 정직하게 검증하였다.

## 2. 제안 프레임워크

제안 프레임워크는 데이터 수집 계층, Temporal RAG 계층, 역할별 평가 계층, 위험 조정 및 후보 순위화 계층으로 구성된다. 데이터 수집 계층은 뉴스, DART 공시, 커뮤니티 텍스트, 가격 및 거래량 시계열, 테마 구성 후보를 입력으로 사용한다. Temporal RAG 계층은 각 재조정일을 `as_of_date`로 두고, 문서의 `published_at` 또는 가격 데이터의 날짜가 해당 시점 이전인 경우에만 검색과 특징 계산에 사용한다. 이때 뉴스와 커뮤니티 데이터에는 제한된 lookback window를 적용하고, 공시는 상대적으로 긴 기간 근거로 유지한다.

역할별 평가 계층에서는 Analyst가 정성 근거와 사업 맥락을, Quant가 수치 및 랭킹 특징을, Chartist가 가격 및 거래량 기반 특징을, Risk Manager가 변동성 및 과열 조건을 검토한다. 이러한 역할 기반 reasoning-and-acting 구조는 LLM이 외부 도구와 근거를 결합해 판단을 구성하는 Agentic AI 접근과 연결된다[2]. 모든 구성 요소가 LLM인 것은 아니며, Chartist와 일부 위험 필터는 규칙 및 수식 기반 특징을 포함한다. 이 점에서 본 구조는 순수 LLM committee가 아니라 LLM 근거 평가와 결정론적 가격 특징을 결합한 hybrid Agentic AI 구조이다.

Fig. 1. Overall process of point-in-time temporal validation.

`News/DART/Forum/Prices -> as_of_date Temporal RAG -> Analyst/Quant/Chartist/Risk Manager -> risk-adjusted score -> candidate ranking -> future holding-period evaluation`

## 3. 실험 설계 및 결과

실험 범위는 AI 산업 테마 내 후보군 선정으로 제한하였다. 운영 시스템에는 다중 테마 paper trading 파이프라인이 존재하지만, 본 논문의 정량 검증은 단일 AI 테마 후보군 선정 문제에 한정한다. 이는 다중 테마 자동매매 성과가 이미 end-to-end로 검증되었다는 과도한 주장과 구분하기 위한 것이다.

비교 대상은 결정론적 후보 선정 기준선, RSI 기반 전략, Bollinger Band 기반 전략, 20일 momentum, 변동성 조정 momentum, LLM-only, hybrid multi-agent이다. 짧은 구간은 주간 재조정과 5거래일 보유, 긴 구간은 월간 재조정과 60거래일 보유로 구성하였다. 비용 조건은 거래비용 15bp, 슬리피지 5bp, 시장충격 5bp를 포함하였다. 또한 변동성, 단기 급등, 시장 breadth, trailing stop 조건을 적용하여 검증 환경의 현실성을 높였다.

구간 해석에서는 2025년을 파라미터 조정과 비교 해석을 위한 tuning/reference 구간으로 처리하고, 2023년, 2024년, 2026Q1을 검증 구간으로 해석한다. 또한 2026Q1 long 결과는 1회 재조정과 3개 포지션 기반의 제한 표본이므로 pilot 결과로만 해석한다.

Table 1. Experimental protocol and realism controls.

| Item | Setting |
|---|---|
| Theme scope | AI industry theme candidate selection |
| Periods | Validation: 2023, 2024, 2026Q1; tuning/reference: 2025 |
| Horizons | Short: 5 trading days, Long: 60 trading days |
| Comparators | Deterministic, RSI, Bollinger Band, momentum, LLM-only, hybrid multi-agent |
| Cost assumptions | Transaction cost 15bp, slippage 5bp, market impact 5bp |
| Risk controls | Volatility filter, overheat filter, breadth filter, trailing stop |

Table 2는 8개 기간-구간 조합의 핵심 결과를 요약한다. hybrid multi-agent는 2023 short, 2024 short, 2025 long, 2026Q1 long에서 결정론적 기준선 이상을 보였다. 최고 기술지표 기준선 이상인 경우는 2025 short, 2025 long, 2026Q1 short, 2026Q1 long의 4건이며, 2023 short는 최고 기술지표 기준선과 근접했지만 초과하지는 못했다. 다만 2023 long, 2024 long처럼 기술지표 또는 결정론적 기준선에 뒤처지는 구간도 존재하였다.

Table 2. Summary of period-horizon comparison.

| Period | Horizon | Hybrid return | Deterministic return | Best technical return |
|---|---:|---:|---:|---:|
| 2023 | Short | 14.92% | 13.58% | 15.15% |
| 2023 | Long | -0.42% | 7.64% | 52.69% |
| 2024 | Short | 46.99% | 38.27% | 167.72% |
| 2024 | Long | 0.08% | 16.78% | 16.02% |
| 2025 | Short | 190.16% | 216.92% | 60.08% |
| 2025 | Long | 103.29% | 82.78% | 94.71% |
| 2026Q1 | Short | 15.40% | 24.62% | 8.55% |
| 2026Q1 | Long | 14.20% | 6.14% | 0.00% |

추가 감사에서는 최근 AI 테마 corpus의 세 `as_of_date` 샘플에 대해 문서의 `published_at`과 가격 데이터의 `timestamp`가 의사결정일을 넘지 않는지 확인하였다. Table 3에서 각 샘플의 최대 문서일과 최대 가격일은 모두 `as_of_date` 이하였고, 미래 날짜 레코드는 검색 및 가격 특징 계산에서 제외되었다.

Table 3. Temporal evidence leakage audit sample.

| as_of_date | Document rows | Max document date | Price rows | Max price date | Future rows excluded (doc/price) |
|---|---:|---|---:|---|---:|
| 2026-05-20 | 847 | 2026-05-20 | 400 | 2026-05-20 | 6151/650 |
| 2026-05-31 | 2130 | 2026-05-31 | 700 | 2026-05-29 | 4868/350 |
| 2026-06-10 | 6998 | 2026-06-10 | 1050 | 2026-06-10 | 0/0 |

결과는 hybrid multi-agent가 모든 상황에서 우월한 보편 전략이 아님을 보여준다. 특히 LLM-only도 2024 short와 2026Q1 short 일부 비교에서 hybrid를 상회하므로, hybrid 구조의 장점은 일관된 수익률 우위가 아니라 역할별 근거 분해와 위험 해석 가능성으로 제한해 해석해야 한다. Table 2는 수익률 중심 요약이며, MDD와 Sharpe 같은 위험 조정 지표는 보조 산출물에서 함께 점검하였다. 오히려 본 검증의 의미는 복수 기준선을 동시에 두어 결과를 과장하지 않고, 각 의사결정 시점에서 어떤 문서와 가격 특징이 사용되었는지 추적할 수 있다는 데 있다. 결정론적 및 기술지표 기준선이 강한 구간은 Agentic AI 결과를 해석하는 하한선 또는 경쟁 기준으로 작동한다. 따라서 본 연구의 결과는 수익률 중심의 자동매매 모델이 아니라 시간 경계가 있는 산업 후보군 검증 방법론으로 해석되어야 한다.

## 4. 결론

본 연구는 동적 산업 테마 후보군 평가에서 Temporal RAG와 역할 분화 Agentic AI를 결합한 point-in-time 검증 프레임워크를 제안하였다. 제안 프레임워크는 의사결정 시점 이전 근거만 사용하도록 문서와 가격 특징을 제한하고, 후보 선정 이후의 미래 수익률을 별도 평가 단계로 분리한다. AI 산업 테마 실험에서 hybrid multi-agent는 8개 기간-구간 조합 중 일부에서 결정론적 및 기술지표 기준선과 경쟁적인 결과를 보였으나, 2025년은 tuning/reference 구간이고 2026Q1 long은 pilot 표본이라는 제약을 갖는다. 모든 구간에서 일관된 우월성을 보이지도 않았다.

따라서 본 연구의 핵심 주장은 성과 보장이 아니라 검증 프로토콜, 근거 추적성, 역할별 의사결정 지원 구조이다. 향후 연구에서는 공식 과거 테마 편입 이력 확보, 다중 테마 end-to-end 백테스트, 장기간 paper trading 로그 기반 운영 검증, 실시간 주문 지연 및 체결 실패를 포함한 실행 품질 평가를 추가할 필요가 있다.

## 감사의 글

본 연구는 개인 연구 프로젝트의 실험 결과를 기반으로 작성되었으며, 별도의 외부 연구비 지원은 없었다.

## 참고문헌

Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., Kuttler, H., Lewis, M., Yih, W., Rocktaschel, T., Riedel, S. and Kiela, D., 2020, Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks, Advances in Neural Information Processing Systems, 33, pp.9459-9474, https://arxiv.org/abs/2005.11401.

Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K. and Cao, Y., 2023, ReAct: Synergizing Reasoning and Acting in Language Models, International Conference on Learning Representations, https://openreview.net/forum?id=WE_vluYUL-X.

Bailey, D.H., Borwein, J.M., Lopez de Prado, M. and Zhu, Q.J., 2017, The Probability of Backtest Overfitting, Journal of Computational Finance, 20(4), pp.39-69, https://doi.org/10.21314/JCF.2016.322.
