export type BacktestHorizon = "short" | "long";

export type BacktestCoverageRow = {
  period: string;
  horizon: BacktestHorizon;
  has_center_multi_agent: boolean;
  has_deterministic: boolean;
  has_rsi: boolean;
  has_bollinger: boolean;
  row_count: number;
  status: string;
};

export type BacktestSummaryRow = {
  period: string;
  horizon: BacktestHorizon;
  center_strategy: string;
  center_return_pct: number;
  center_mdd_pct: number;
  deterministic_strategy: string;
  deterministic_return_pct: number;
  best_rsi_bollinger_strategy: string;
  best_rsi_bollinger_return_pct: number;
  best_technical_strategy: string;
  best_technical_return_pct: number;
  winner_strategy: string;
  winner_group: string;
  winner_return_pct: number;
  verdict: string;
};

export type BacktestComparisonRow = {
  period: string;
  source_period: string;
  period_role: string;
  horizon: BacktestHorizon;
  strategy_id: string;
  strategy_group: string;
  strategy_label: string;
  rebalance: string;
  top_n: number;
  hold_days: number;
  llm_weight: number | null;
  llm_scope: string | null;
  traded_rebalance_count: number;
  position_count: number;
  total_return_pct: number;
  benchmark_return_pct: number;
  excess_return_pct: number;
  mdd_pct: number;
  sharpe: number;
  win_rate_pct: number | null;
  prediction_hit_rate_pct: number | null;
  status: string;
  result_json: string;
  center_multi_agent_strategy: string;
  is_center_multi_agent: boolean;
  center_total_return_pct: number;
  center_excess_return_pct: number;
  center_mdd_pct: number;
  return_delta_vs_center_multi_agent_pct: number;
  excess_delta_vs_center_multi_agent_pct: number;
  mdd_delta_vs_center_multi_agent_pct: number;
  beats_center_return: boolean;
};

export type BacktestComparison = {
  generated_at: string;
  theme: string;
  center_strategy: {
    short: string;
    long: string;
  };
  row_count: number;
  coverage: BacktestCoverageRow[];
  summary: BacktestSummaryRow[];
  rows: BacktestComparisonRow[];
  artifacts: Record<string, string>;
};

export type BacktestComparisonBundle = BacktestComparison & {
  reportMarkdown: string;
};
