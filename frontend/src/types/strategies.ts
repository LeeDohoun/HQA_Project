// Contract: docs/strategy-runtime.md, "화면용 GET /api/v1/strategies" and "상태 형식".

export type StrategyStage = "observe" | "shadow" | "paper";

export type StrategyVerdict = {
  verdict: string | null;
  recordedAt: string | null;
};

export type StrategyHoldoutVerdict = StrategyVerdict & {
  experimentId: string | null;
  variant: string | null;
};

export type StrategyDecision = {
  month: string;
  decisionDate: string;
  tradeDate: string;
  exitDate: string | null;
  targetCount: number;
  recordHash: string;
  label: string;
};

export type StrategyShadowPoint = {
  date: string;
  navKrw: number;
};

export type StrategyShadow = {
  mode: StrategyStage;
  sessions: number;
  firstDate: string | null;
  lastDate: string | null;
  initialNavKrw: number;
  navKrw: number;
  returnPct: number;
  incidents: number;
  history: StrategyShadowPoint[];
};

export type StrategyPhase = "OPEN" | "CLOSE";

export type StrategyPlan = {
  sessionDate: string;
  phase: StrategyPhase;
  buyCount: number;
  sellCount: number;
  buyNotionalKrw: number;
  sellNotionalKrw: number;
  advWarnings: number;
};

export type StrategyStatusPayload = {
  strategyId: string;
  title: string;
  description: string;
  generatedAt: string;
  stage: { requested: StrategyStage; effective: StrategyStage; reasons: string[] };
  research: {
    experimentId: string;
    variant: string;
    design: StrategyVerdict;
    holdout: StrategyHoldoutVerdict;
  };
  decision: StrategyDecision | null;
  shadow: StrategyShadow;
  plan: StrategyPlan | null;
  account: { userId: string | null; bound: boolean };
};

export type StrategyBatchStatus =
  | "VALIDATED" | "QUEUED" | "SUBMITTING" | "SUBMITTED" | "COMPLETED" | "PARTIAL" | "REJECTED" | "HALTED";

export type StrategyLatestBatch = {
  batchId: string;
  phase: StrategyPhase;
  sessionDate: string;
  status: StrategyBatchStatus;
  orderCount: number;
  filledCount: number;
  rejectedCount: number;
  avgSlippagePct: number | null;
};

export type StrategyRiskLimits = {
  dailyLossLimitPct: number;
  drawdownStopPct: number;
  fullReviewPct: number;
  maxOrdersPerDay: number;
  maxCancelRatio: number;
  maxOrderPctOfEquity: number;
};

export type StrategyRisk = {
  userId: string;
  equityKrw: number;
  dailyPnlPct: number;
  peakEquityKrw: number;
  drawdownPct: number;
  buyAllowed: boolean;
  fullReviewRequired: boolean;
  halted: boolean;
  reasons: string[];
  ordersToday: number;
  cancelsToday: number;
  limits: StrategyRiskLimits;
};

export type StrategySummary = {
  strategyId: string;
  updatedAt: string;
  payload: StrategyStatusPayload;
  latestBatch: StrategyLatestBatch | null;
  risk: StrategyRisk | null;
};

export type StrategyHalt = {
  scope: string;
  halted: boolean;
  reason: string | null;
  updatedAt: string;
};

export type StrategiesResponse = {
  strategies: StrategySummary[];
  halts: StrategyHalt[];
};
