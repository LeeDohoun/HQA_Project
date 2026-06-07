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
      "HQA는 AI 멀티에이전트가 종목을 분석하고, 그 판단을 바탕으로 매매 전략을 제안·검증하는 퀀트 서비스입니다.",
      "Analyst, Quant, Chartist, RiskManager 네 개의 전문 에이전트가 각자의 관점으로 분석한 뒤 Supervisor가 의견을 종합합니다."
    ]
  },
  {
    id: "why-4-agent",
    question: "왜 4-agent(멀티에이전트) 구조를 쓰나요?",
    answer: [
      "단일 에이전트나 일부 에이전트를 제거한 구조보다, 4개 에이전트 + Supervisor 합의 구조가 더 안정적인 성과를 냈기 때문입니다.",
      "아래는 2024년 validation 구간에서 대표 4-Agent 구조와 여러 변형(단일 에이전트, 특정 에이전트 제거, 에이전트 추가)을 같은 조건으로 비교한 결과입니다. 대표 4-Agent는 단타에서 모든 변형 중 가장 높은 초과수익률을 기록했고, 어느 에이전트 하나라도 빼면 성과가 눈에 띄게 떨어집니다."
    ]
  },
  {
    id: "agents-roles",
    question: "각 에이전트는 무슨 역할을 하나요?",
    answer: [
      "Analyst는 기업·테마의 펀더멘털과 뉴스 흐름을, Quant는 재무·가격 지표 기반의 정량 신호를 분석합니다.",
      "Chartist는 차트·기술적 패턴을, RiskManager는 변동성과 하방 위험을 점검합니다. Supervisor가 이들의 의견을 종합해 최종 판단을 만듭니다.",
      "비교 실험에서 Chartist를 제거하면 성과가 가장 크게 무너졌고, RiskManager를 빼면 최대낙폭(MDD) 관리가 약해졌습니다."
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
      subtitle="HQA의 동작 방식과 왜 4-agent 구조를 선택했는지 설명합니다."
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
