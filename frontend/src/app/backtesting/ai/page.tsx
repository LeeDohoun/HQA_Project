"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/common/app-shell";
import { StatusPill } from "@/components/common/status-pill";
import { loadAiBacktestComparison } from "@/lib/backtesting";
import type {
  BacktestComparisonBundle,
  BacktestComparisonRow,
  BacktestHorizon,
  BacktestSummaryRow
} from "@/types/backtesting";

type FilterGroup = "all" | "multi_agent" | "deterministic" | "technical";
type FilterHorizon = "all" | BacktestHorizon;

type ChartSeries<T> = {
  label: string;
  color: string;
  value: (row: T) => number;
};

type RiskSummaryRow = BacktestSummaryRow & {
  center_mdd_depth_pct: number;
  deterministic_mdd_depth_pct: number;
  best_rsi_bollinger_mdd_depth_pct: number;
};

const HORIZON_LABELS: Record<BacktestHorizon, string> = {
  short: "단타",
  long: "장타"
};

const GROUP_FILTERS: { value: FilterGroup; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "multi_agent", label: "multi-agent" },
  { value: "deterministic", label: "규칙기반" },
  { value: "technical", label: "기본전략" }
];

const HORIZON_FILTERS: { value: FilterHorizon; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "short", label: "단타" },
  { value: "long", label: "장타" }
];

const RETURN_SERIES: ChartSeries<BacktestSummaryRow>[] = [
  { label: "multi-agent", color: "#3182f6", value: (row) => row.center_return_pct },
  { label: "규칙기반", color: "#64748b", value: (row) => row.deterministic_return_pct },
  { label: "RSI/볼밴", color: "#10b981", value: (row) => row.best_rsi_bollinger_return_pct }
];

const RISK_SERIES: ChartSeries<RiskSummaryRow>[] = [
  { label: "multi-agent", color: "#3182f6", value: (row) => row.center_mdd_depth_pct },
  { label: "규칙기반", color: "#64748b", value: (row) => row.deterministic_mdd_depth_pct },
  { label: "RSI/볼밴", color: "#10b981", value: (row) => row.best_rsi_bollinger_mdd_depth_pct }
];

export default function AiBacktestPage() {
  const [comparison, setComparison] = useState<BacktestComparisonBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [groupFilter, setGroupFilter] = useState<FilterGroup>("all");
  const [horizonFilter, setHorizonFilter] = useState<FilterHorizon>("all");

  useEffect(() => {
    let active = true;

    loadAiBacktestComparison()
      .then((result) => {
        if (active) setComparison(result);
      })
      .catch((loadError) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "백테스트 결과를 불러오지 못했습니다.");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const metrics = useMemo(() => {
    if (!comparison) return null;

    const total = comparison.summary.length;
    const completedCoverage = comparison.coverage.filter((row) => row.status === "완료").length;
    const beatsRsiBollinger = comparison.summary.filter(
      (row) => row.center_return_pct >= row.best_rsi_bollinger_return_pct
    ).length;
    const beatsDeterministic = comparison.summary.filter(
      (row) => row.center_return_pct >= row.deterministic_return_pct
    ).length;
    const bestCenter = [...comparison.summary].sort((a, b) => b.center_return_pct - a.center_return_pct)[0];
    const worstCenter = [...comparison.summary].sort((a, b) => a.center_return_pct - b.center_return_pct)[0];

    return {
      total,
      completedCoverage,
      beatsRsiBollinger,
      beatsDeterministic,
      bestCenter,
      worstCenter
    };
  }, [comparison]);

  const verdictCounts = useMemo(() => {
    if (!comparison) return { best: 0, mixed: 0, lagged: 0 };
    return comparison.summary.reduce(
      (acc, row) => {
        if (row.verdict === "multi_agent_best_or_tied") acc.best += 1;
        else if (row.verdict === "multi_agent_lagged") acc.lagged += 1;
        else acc.mixed += 1;
        return acc;
      },
      { best: 0, mixed: 0, lagged: 0 }
    );
  }, [comparison]);

  const riskRows = useMemo<RiskSummaryRow[]>(() => {
    if (!comparison) return [];

    return comparison.summary.map((summaryRow) => {
      const deterministic = findStrategyRow(comparison.rows, summaryRow, summaryRow.deterministic_strategy);
      const bestRsiBollinger = findStrategyRow(comparison.rows, summaryRow, summaryRow.best_rsi_bollinger_strategy);

      return {
        ...summaryRow,
        center_mdd_depth_pct: Math.abs(summaryRow.center_mdd_pct ?? 0),
        deterministic_mdd_depth_pct: Math.abs(deterministic?.mdd_pct ?? 0),
        best_rsi_bollinger_mdd_depth_pct: Math.abs(bestRsiBollinger?.mdd_pct ?? 0)
      };
    });
  }, [comparison]);

  const filteredRows = useMemo(() => {
    if (!comparison) return [];

    return comparison.rows.filter((row) => {
      const groupMatch = groupFilter === "all" || groupBucket(row.strategy_group) === groupFilter;
      const horizonMatch = horizonFilter === "all" || row.horizon === horizonFilter;
      return groupMatch && horizonMatch;
    });
  }, [comparison, groupFilter, horizonFilter]);

  return (
    <AppShell
      title="AI 백테스트 비교"
      subtitle="multi-agent 중심 전략과 규칙기반, RSI, 볼린저밴드 전략을 같은 기간으로 비교합니다."
      wide
      actions={
        <>
          <Link className="button-ghost" href="/dashboard">대시보드</Link>
          <Link className="button-ghost" href="/backtesting/ai-strategy-comparison.json" target="_blank">원본 JSON</Link>
        </>
      }
    >
      {loading ? <div className="empty-state">백테스트 결과를 불러오는 중입니다.</div> : null}
      {error ? <p className="error-text">{error}</p> : null}

      {comparison && metrics ? (
        <div className="backtest-page">
          <section className="backtest-summary-grid">
            <MetricCard
              label="테스트 완료"
              value={`${metrics.completedCoverage}/${comparison.coverage.length}`}
              detail={`${comparison.theme} 테마, ${comparison.row_count}개 결과 행`}
            />
            <MetricCard
              label="RSI/볼밴 대비"
              value={`${metrics.beatsRsiBollinger}/${metrics.total}`}
              detail="multi-agent 수익률이 RSI/볼밴 최고 전략 이상"
            />
            <MetricCard
              label="규칙기반 대비"
              value={`${metrics.beatsDeterministic}/${metrics.total}`}
              detail="multi-agent 수익률이 deterministic 이상"
            />
            <MetricCard
              label="최고 구간"
              value={formatPercent(metrics.bestCenter.center_return_pct)}
              detail={`${metrics.bestCenter.period} ${HORIZON_LABELS[metrics.bestCenter.horizon]}`}
            />
            <MetricCard
              label="최저 구간"
              value={formatPercent(metrics.worstCenter.center_return_pct)}
              detail={`${metrics.worstCenter.period} ${HORIZON_LABELS[metrics.worstCenter.horizon]}`}
            />
          </section>

          <section className="panel backtest-conclusion">
            <div>
              <h2>핵심 판정</h2>
              <p>
                현재 AI 테마 결과만 보면 multi-agent hybrid는 기본 전략보다 나은 구간이 있지만 항상 우월하지는 않습니다.
                장타는 2025년과 2026년 1분기에서 강했고, 2023년과 2024년 장타에서는 기본 전략과 규칙기반에 밀렸습니다.
              </p>
            </div>
            <div className="verdict-stack" aria-label="판정 분포">
              <VerdictBar label="multi-agent 우위" count={verdictCounts.best} total={metrics.total} tone="good" />
              <VerdictBar label="혼합" count={verdictCounts.mixed} total={metrics.total} tone="warn" />
              <VerdictBar label="multi-agent 열위" count={verdictCounts.lagged} total={metrics.total} tone="bad" />
            </div>
          </section>

          <section className="panel">
            <PanelHeader title="기간별 수익률" subtitle="각 기간에서 multi-agent 중심 전략, 규칙기반, RSI/볼밴 최고 전략을 비교합니다." />
            <GroupedBarChart rows={comparison.summary} series={RETURN_SERIES} valueSuffix="%" />
          </section>

          <section className="panel">
            <PanelHeader title="최대낙폭 비교" subtitle="막대가 낮을수록 중간 손실 부담이 작습니다." />
            <GroupedBarChart rows={riskRows} series={RISK_SERIES} valueSuffix="%" />
          </section>

          <section className="panel">
            <PanelHeader title="핵심 기간별 요약" subtitle="최종 판정은 multi-agent 중심 전략을 기준으로 계산했습니다." />
            <div className="backtest-table-wrap">
              <table className="backtest-table">
                <thead>
                  <tr>
                    <th>기간</th>
                    <th>구간</th>
                    <th>multi-agent</th>
                    <th>규칙기반</th>
                    <th>RSI/볼밴 최고</th>
                    <th>승자</th>
                    <th>판정</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.summary.map((row) => (
                    <tr key={`${row.period}-${row.horizon}`}>
                      <td>{row.period}</td>
                      <td>{HORIZON_LABELS[row.horizon]}</td>
                      <td>
                        <strong>{row.center_strategy}</strong>
                        <span>{formatPercent(row.center_return_pct)}</span>
                      </td>
                      <td>
                        <strong>{row.deterministic_strategy}</strong>
                        <span>{formatPercent(row.deterministic_return_pct)}</span>
                      </td>
                      <td>
                        <strong>{row.best_rsi_bollinger_strategy}</strong>
                        <span>{formatPercent(row.best_rsi_bollinger_return_pct)}</span>
                      </td>
                      <td>
                        <strong>{row.winner_strategy}</strong>
                        <span>{formatPercent(row.winner_return_pct)}</span>
                      </td>
                      <td><StatusPill label={verdictLabel(row.verdict)} tone={verdictTone(row.verdict)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <div className="backtest-table-head">
              <PanelHeader title="전체 비교표" subtitle={`${filteredRows.length}개 행 표시`} />
              <div className="backtest-filter-row">
                <SegmentedControl items={GROUP_FILTERS} value={groupFilter} onChange={setGroupFilter} />
                <SegmentedControl items={HORIZON_FILTERS} value={horizonFilter} onChange={setHorizonFilter} />
              </div>
            </div>
            <div className="backtest-table-wrap">
              <table className="backtest-table backtest-table-dense">
                <thead>
                  <tr>
                    <th>기간</th>
                    <th>구간</th>
                    <th>전략</th>
                    <th>그룹</th>
                    <th>수익률</th>
                    <th>벤치마크</th>
                    <th>초과수익</th>
                    <th>MDD</th>
                    <th>샤프</th>
                    <th>중심 대비</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((row) => (
                    <tr key={`${row.period}-${row.horizon}-${row.strategy_id}`}>
                      <td>{row.period}</td>
                      <td>{HORIZON_LABELS[row.horizon]}</td>
                      <td>
                        <strong>{row.strategy_id}</strong>
                        {row.is_center_multi_agent ? <span className="mini-badge">center</span> : null}
                      </td>
                      <td>{groupLabel(row.strategy_group)}</td>
                      <td className={signedClass(row.total_return_pct)}>{formatPercent(row.total_return_pct)}</td>
                      <td className={signedClass(row.benchmark_return_pct)}>{formatPercent(row.benchmark_return_pct)}</td>
                      <td className={signedClass(row.excess_return_pct)}>{formatPercent(row.excess_return_pct)}</td>
                      <td className={row.mdd_pct < 0 ? "value-bad" : ""}>{formatPercent(row.mdd_pct)}</td>
                      <td>{formatNumber(row.sharpe, 2)}</td>
                      <td className={signedClass(row.return_delta_vs_center_multi_agent_pct)}>
                        {formatPercent(row.return_delta_vs_center_multi_agent_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <PanelHeader title="리포트 원문" subtitle={`생성 시각 ${comparison.generated_at}`} />
            <pre className="markdown-report">{comparison.reportMarkdown}</pre>
          </section>
        </div>
      ) : null}
    </AppShell>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="backtest-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </div>
  );
}

function PanelHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="panel-head">
      <h2>{title}</h2>
      {subtitle ? <p>{subtitle}</p> : null}
    </div>
  );
}

function VerdictBar({ label, count, total, tone }: { label: string; count: number; total: number; tone: "good" | "warn" | "bad" }) {
  const pct = total > 0 ? (count / total) * 100 : 0;

  return (
    <div className="verdict-bar">
      <div>
        <span>{label}</span>
        <strong>{count}/{total}</strong>
      </div>
      <div className="verdict-track">
        <span className={`verdict-fill verdict-fill-${tone}`} style={{ width: `${pct}%` }} />
      </div>
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

function GroupedBarChart<T extends { period: string; horizon: BacktestHorizon }>({
  rows,
  series,
  valueSuffix
}: {
  rows: T[];
  series: ChartSeries<T>[];
  valueSuffix: string;
}) {
  const chartHeight = 220;
  const top = 22;
  const bottom = 56;
  const left = 44;
  const right = 24;
  const groupWidth = 118;
  const barWidth = 14;
  const barGap = 6;
  const width = Math.max(840, left + right + rows.length * groupWidth);
  const height = top + chartHeight + bottom;
  const values = rows.flatMap((row) => series.map((item) => item.value(row)));
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(0, ...values);
  const span = maxValue - minValue || 1;
  const y = (value: number) => top + ((maxValue - value) / span) * chartHeight;
  const zeroY = y(0);
  const tickValues = [0, 0.25, 0.5, 0.75, 1].map((step) => minValue + span * step);

  return (
    <div className="backtest-chart-block">
      <div className="chart-legend">
        {series.map((item) => (
          <span key={item.label}>
            <i style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>
      <div className="backtest-chart-scroll">
        <svg className="backtest-chart" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img">
          {tickValues.map((tick) => {
            const lineY = y(tick);
            return (
              <g key={tick.toFixed(4)}>
                <line x1={left} x2={width - right} y1={lineY} y2={lineY} />
                <text x={8} y={lineY + 4}>{formatNumber(tick, 0)}{valueSuffix}</text>
              </g>
            );
          })}

          <line className="chart-zero" x1={left} x2={width - right} y1={zeroY} y2={zeroY} />

          {rows.map((row, rowIndex) => {
            const groupX = left + rowIndex * groupWidth + 24;
            const barsWidth = series.length * barWidth + (series.length - 1) * barGap;
            const labelX = groupX + barsWidth / 2;

            return (
              <g key={`${row.period}-${row.horizon}`}>
                {series.map((item, seriesIndex) => {
                  const value = item.value(row);
                  const valueY = y(value);
                  const barY = value >= 0 ? valueY : zeroY;
                  const barHeight = Math.max(2, Math.abs(zeroY - valueY));
                  const x = groupX + seriesIndex * (barWidth + barGap);

                  return (
                    <rect
                      fill={item.color}
                      height={barHeight}
                      key={item.label}
                      rx={4}
                      width={barWidth}
                      x={x}
                      y={barY}
                    >
                      <title>{`${row.period} ${HORIZON_LABELS[row.horizon]} ${item.label}: ${formatNumber(value, 2)}${valueSuffix}`}</title>
                    </rect>
                  );
                })}
                <text className="chart-x-label" textAnchor="middle" x={labelX} y={height - 28}>{row.period}</text>
                <text className="chart-x-sub" textAnchor="middle" x={labelX} y={height - 12}>{HORIZON_LABELS[row.horizon]}</text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function findStrategyRow(rows: BacktestComparisonRow[], summaryRow: BacktestSummaryRow, strategyId: string) {
  return rows.find((row) => (
    row.period === summaryRow.period &&
    row.horizon === summaryRow.horizon &&
    row.strategy_id === strategyId
  ));
}

function groupBucket(group: string): Exclude<FilterGroup, "all"> {
  if (group.startsWith("multi_agent")) return "multi_agent";
  if (group === "deterministic") return "deterministic";
  return "technical";
}

function groupLabel(group: string) {
  if (group === "multi_agent_hybrid") return "multi-agent hybrid";
  if (group === "multi_agent_llm_only") return "multi-agent LLM";
  if (group === "deterministic") return "규칙기반";
  return "기본전략";
}

function verdictLabel(verdict: string) {
  if (verdict === "multi_agent_best_or_tied") return "multi-agent 우위";
  if (verdict === "multi_agent_lagged") return "multi-agent 열위";
  return "혼합";
}

function verdictTone(verdict: string): "good" | "warn" | "bad" | "neutral" {
  if (verdict === "multi_agent_best_or_tied") return "good";
  if (verdict === "multi_agent_lagged") return "bad";
  if (verdict === "mixed") return "warn";
  return "neutral";
}

function formatPercent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "-";
  const formatted = new Intl.NumberFormat("ko-KR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(value);
  return `${formatted}%`;
}

function formatNumber(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return "-";
  return new Intl.NumberFormat("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  }).format(value);
}

function signedClass(value: number) {
  if (value > 0) return "value-good";
  if (value < 0) return "value-bad";
  return "";
}
