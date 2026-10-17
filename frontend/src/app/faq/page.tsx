"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/common/app-shell";
import { StatusPill } from "@/components/common/status-pill";
import { loadAgentArchitectureComparison } from "@/lib/backtesting";
import type {
  AgentArchitectureComparison,
  AgentArchitectureRow,
  BacktestHorizon
} from "@/types/backtesting";

type FaqItem = {
  id: string;
  question: string;
  answer: string[];
};

const HORIZON_FILTERS: { value: BacktestHorizon; label: string }[] = [
  { value: "short", label: "단타" },
  { value: "long", label: "장타" }
];

const FAQ_ITEMS: FaqItem[] = [
  {
    id: "what-is-hqa",
    question: "HQA는 어떤 서비스인가요?",
    answer: [
      "HQA는 한국 주식의 공시·뉴스·가격을 분석하고, 모의투자로 판단을 검증하는 AI 리서치 워크스페이스입니다.",
      "수동 종목 분석은 Analyst·Quant·Chartist 세 전문가가 담당합니다. 계좌별 RiskManager 판단과 주문은 별도의 모의 자동매매 흐름에서 수행합니다."
    ]
  },
  {
    id: "why-4-agent",
    question: "종목 분석과 매매 판단은 어떻게 다른가요?",
    answer: [
      "종목 분석은 기업에 대한 공통 근거를 만들고, 계좌별 매매 판단은 보유 자산과 위험 한도를 함께 고려합니다. 분석을 실행하는 것만으로 주문이 나가지는 않습니다.",
      "아래 구조 비교는 과거 2024년 validation 연구 결과입니다. 현재 HQA 엔진의 수익률이나 운영 성능을 나타내지 않습니다."
    ]
  },
  {
    id: "agents-roles",
    question: "각 에이전트는 무슨 역할을 하나요?",
    answer: [
      "Analyst는 기업·테마의 펀더멘털과 뉴스 흐름을, Quant는 재무·가격 지표 기반의 정량 신호를 분석합니다.",
      "Chartist는 가격·거래량의 기술적 패턴을 분석합니다. RiskManager는 자동매매 흐름에서 계좌별 위험을 별도로 판단하며, 백엔드가 주문 조건과 위험 한도를 검증합니다.",
      "데이터가 누락되거나 분석이 실패하면 해당 상태와 근거 부족을 표시합니다. 빈 데이터를 중립 점수로 바꾸지 않습니다."
    ]
  },
  {
    id: "backtest-meaning",
    question: "백테스트 결과는 어떻게 해석하나요?",
    answer: [
      "백테스트는 과거 데이터로 전략을 모의 운용해 본 결과입니다. 수익률, 초과수익률(벤치마크 대비), 최대낙폭(MDD)을 함께 봅니다.",
      "여러 기간(2023·2024·2025·2026Q1)과 단타/장타를 나눠 비교하므로, 특정 구간의 운에 의존하지 않는지 확인할 수 있습니다.",
      "자세한 기간별 결과는 AI 백테스트 비교 페이지에서 확인할 수 있습니다."
    ]
  },
  {
    id: "guarantee",
    question: "백테스트 성과가 미래 수익을 보장하나요?",
    answer: [
      "아니요. 백테스트는 과거 데이터 기반의 검증이며 미래 수익을 보장하지 않습니다.",
      "시장 상황에 따라 손실이 발생할 수 있으므로, 투자 판단의 최종 책임은 사용자 본인에게 있습니다."
    ]
  }
];

export default function FaqPage() {
  const [comparison, setComparison] = useState<AgentArchitectureComparison | null>(null);
  const [error, setError] = useState("");
  const [horizon, setHorizon] = useState<BacktestHorizon>("short");

  useEffect(() => {
    let active = true;

    loadAgentArchitectureComparison()
      .then((result) => {
        if (active) setComparison(result);
      })
      .catch((loadError) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "비교 결과를 불러오지 못했습니다.");
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const rows = useMemo<AgentArchitectureRow[]>(() => {
    if (!comparison) return [];
    return comparison.horizons[horizon] ?? [];
  }, [comparison, horizon]);

  return (
    <AppShell
      title="자주 묻는 질문"
      subtitle="현재 HQA의 분석·모의투자 흐름과 과거 연구 결과를 설명합니다."
      actions={
        <>
          <Link className="button-ghost" href="/dashboard">대시보드</Link>
          <Link className="button-ghost" href="/backtesting/ai">백테스트 비교</Link>
        </>
      }
    >
      <div className="faq-page">
        {FAQ_ITEMS.map((item) => (
          <section className="panel faq-item" id={item.id} key={item.id}>
            <h2 className="faq-question">{item.question}</h2>
            {item.answer.map((paragraph, index) => (
              <p className="faq-answer" key={index}>
                {paragraph}
              </p>
            ))}

            {item.id === "why-4-agent" ? (
              <div className="faq-evidence">
                {error ? <p className="error-text">{error}</p> : null}
                {!comparison && !error ? (
                  <div className="empty-state">비교 결과를 불러오는 중입니다.</div>
                ) : null}

                {comparison ? (
                  <>
                    <div className="faq-evidence-head">
                      <span className="faq-evidence-caption">
                        {comparison.theme} 테마 · {comparison.period_note} 기준 에이전트 구조 비교
                      </span>
                      <SegmentedControl items={HORIZON_FILTERS} value={horizon} onChange={setHorizon} />
                    </div>

                    <ArchitectureBars rows={rows} />

                    <div className="backtest-table-wrap">
                      <table className="backtest-table">
                        <thead>
                          <tr>
                            <th>순위</th>
                            <th>구조</th>
                            <th>총수익률</th>
                            <th>벤치마크</th>
                            <th>초과수익</th>
                            <th>MDD</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((row, index) => (
                            <tr key={row.name}>
                              <td>{index + 1}</td>
                              <td>
                                <strong>{row.label}</strong>
                                {row.is_representative ? (
                                  <StatusPill label="대표 구조" tone="good" />
                                ) : null}
                                {row.note ? <span className="faq-row-note">{row.note}</span> : null}
                              </td>
                              <td className={signedClass(row.total_return_pct)}>
                                {formatPercent(row.total_return_pct)}
                              </td>
                              <td className={signedClass(row.benchmark_return_pct)}>
                                {formatPercent(row.benchmark_return_pct)}
                              </td>
                              <td className={signedClass(row.excess_return_pct)}>
                                {formatPercent(row.excess_return_pct)}
                              </td>
                              <td className={row.mdd_pct < 0 ? "value-bad" : ""}>
                                {formatPercent(row.mdd_pct)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : null}
              </div>
            ) : null}
          </section>
        ))}
      </div>
    </AppShell>
  );
}

function ArchitectureBars({ rows }: { rows: AgentArchitectureRow[] }) {
  if (rows.length === 0) return null;

  const values = rows.map((row) => row.excess_return_pct);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const zeroOffset = ((0 - min) / span) * 100;

  return (
    <div className="faq-bar-list" aria-label="구조별 초과수익률">
      {rows.map((row) => {
        const value = row.excess_return_pct;
        const width = (Math.abs(value) / span) * 100;
        const left = value >= 0 ? zeroOffset : ((value - min) / span) * 100;
        const tone = row.is_representative ? "rep" : value >= 0 ? "pos" : "neg";

        return (
          <div className="faq-bar-row" key={row.name}>
            <span className="faq-bar-name" title={row.label}>
              {row.label}
            </span>
            <span className="faq-bar-track">
              <span
                className={`faq-bar-fill faq-bar-fill-${tone}`}
                style={{ left: `${left}%`, width: `${Math.max(2, width)}%` }}
              />
            </span>
            <span className={`faq-bar-value ${signedClass(value)}`}>{formatPercent(value)}</span>
          </div>
        );
      })}
    </div>
  );
}

function SegmentedControl<T extends string>({
  items,
  value,
  onChange
}: {
  items: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented-control">
      {items.map((item) => (
        <button
          className={item.value === value ? "active" : ""}
          key={item.value}
          onClick={() => onChange(item.value)}
          type="button"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function formatPercent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "-";
  const formatted = new Intl.NumberFormat("ko-KR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(value);
  return `${formatted}%`;
}

function signedClass(value: number) {
  if (value > 0) return "value-good";
  if (value < 0) return "value-bad";
  return "";
}
