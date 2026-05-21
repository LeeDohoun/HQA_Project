import type {
  AnalysisRequest,
  AnalysisResult,
  AnalysisTaskResponse,
  AnalysisProgressEvent,
  ApiError,
  AuthResponse,
  AuthUser,
  CandleHistory,
  KisCredentials,
  KisCredentialsStatus,
  KisVerificationResult,
  LoginRequest,
  RealtimePrice,
  ScoreDetail,
  SignupRequest,
  StockInfo,
  StockSearchResponse,
  UserPreference
} from "@/types/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

if (process.env.NODE_ENV === "production" && API_BASE.startsWith("http://")) {
  throw new Error(
    "NEXT_PUBLIC_API_BASE must use https:// in production — refusing to send credentials over plain HTTP"
  );
}

function extractErrorMessage(body: unknown): string {
  if (typeof body === "string" && body.trim()) {
    return body;
  }

  if (typeof body !== "object" || body === null) {
    return "Request failed";
  }

  if ("message" in body && typeof body.message === "string" && body.message.trim()) {
    return body.message;
  }

  if ("detail" in body) {
    const detail = body.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (typeof detail === "object" && detail !== null && "message" in detail && typeof detail.message === "string" && detail.message.trim()) {
      return detail.message;
    }
  }

  return "Request failed";
}

async function parseResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const error = new Error(extractErrorMessage(body)) as ApiError;
    error.status = response.status;
    error.payload = body;
    throw error;
  }

  return body as T;
}

type AuthUserWire = {
  id: string;
  user_id: string;
  first_name: string;
  last_name: string;
  role: "user" | "admin";
  active: boolean;
  kis_configured: boolean;
  survey_completed: boolean;
  created_at: string;
};

type AuthResponseWire = {
  success: boolean;
  message: string;
  user: AuthUserWire | null;
};

type KisCredentialsStatusWire = {
  configured: boolean;
  kis_app_key_masked: string | null;
  kis_account_no_masked: string | null;
  kis_account_product_code: string | null;
  kis_is_real: boolean;
};

function mapKisStatus(wire: KisCredentialsStatusWire): KisCredentialsStatus {
  return {
    configured: wire.configured,
    kisAppKeyMasked: wire.kis_app_key_masked,
    kisAccountNoMasked: wire.kis_account_no_masked,
    kisAccountProductCode: wire.kis_account_product_code,
    kisIsReal: !!wire.kis_is_real
  };
}

type KisVerificationResultWire = {
  ok: boolean;
  token_ok: boolean;
  account_ok: boolean;
  stage: string;
  message: string;
};

function mapKisVerification(wire: KisVerificationResultWire): KisVerificationResult {
  return {
    ok: wire.ok,
    tokenOk: wire.token_ok,
    accountOk: wire.account_ok,
    stage: wire.stage,
    message: wire.message
  };
}

type UserPreferenceWire = {
  total_assets: number;
  monthly_investment: number;
  investment_period_months: number;
  target_return_rate: number;
  investment_goal: string;
  investment_experience: string;
  birth_date: string;
  investment_type: string;
  volatility_tolerance: string;
  loss_action: string;
  leverage_allowed: boolean;
  occupation_type: string;
  loss_tolerance: string;
  updated_at?: string;
};

type AnalysisTaskResponseWire = {
  task_id: string;
  status: AnalysisTaskResponse["status"];
  message: string;
  estimated_time_seconds: number;
};

type ScoreDetailWire = {
  agent: string;
  total_score: number;
  max_score: number;
  grade: string | null;
  opinion: string | null;
  details: Record<string, unknown>;
};

type AnalysisResultWire = {
  task_id: string;
  status: AnalysisResult["status"];
  stock: StockInfo;
  mode: AnalysisResult["mode"];
  scores: ScoreDetailWire[];
  final_decision: Record<string, unknown>;
  research_quality: string | null;
  quality_warnings: string[];
  created_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  errors: Record<string, string>;
};

type RealtimePriceWire = {
  stock: StockInfo;
  current_price: number;
  change: number;
  change_rate: number;
  open_price: number;
  high_price: number;
  low_price: number;
  volume: number;
  market_cap: number | null;
  per: number | null;
  pbr: number | null;
  timestamp: string;
};

type CandleWire = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  complete: boolean | null;
};

type CandleHistoryWire = {
  stock_code: string;
  timeframe: string;
  candles: CandleWire[];
  has_more: boolean;
};

function mapAuthUser(user: AuthUserWire): AuthUser {
  return {
    id: user.id,
    userId: user.user_id,
    firstName: user.first_name,
    lastName: user.last_name,
    role: user.role,
    active: user.active,
    kisConfigured: user.kis_configured,
    surveyCompleted: user.survey_completed,
    createdAt: user.created_at
  };
}

function mapAuthResponse(response: AuthResponseWire): AuthResponse {
  return {
    success: response.success,
    message: response.message,
    user: response.user ? mapAuthUser(response.user) : null
  };
}

function mapPreference(response: UserPreferenceWire): UserPreference {
  return {
    totalAssets: response.total_assets,
    monthlyInvestment: response.monthly_investment,
    investmentPeriodMonths: response.investment_period_months,
    targetReturnRate: response.target_return_rate,
    investmentGoal: response.investment_goal,
    investmentExperience: response.investment_experience,
    birthDate: response.birth_date,
    investmentType: response.investment_type,
    volatilityTolerance: response.volatility_tolerance,
    lossAction: response.loss_action,
    leverageAllowed: response.leverage_allowed,
    occupationType: response.occupation_type,
    lossTolerance: response.loss_tolerance,
    updatedAt: response.updated_at
  };
}

function toPreferenceWire(payload: UserPreference): UserPreferenceWire {
  return {
    total_assets: payload.totalAssets,
    monthly_investment: payload.monthlyInvestment,
    investment_period_months: payload.investmentPeriodMonths,
    target_return_rate: payload.targetReturnRate,
    investment_goal: payload.investmentGoal,
    investment_experience: payload.investmentExperience,
    birth_date: payload.birthDate,
    investment_type: payload.investmentType,
    volatility_tolerance: payload.volatilityTolerance,
    loss_action: payload.lossAction,
    leverage_allowed: payload.leverageAllowed,
    occupation_type: payload.occupationType,
    loss_tolerance: payload.lossTolerance
  };
}

function mapTask(response: AnalysisTaskResponseWire): AnalysisTaskResponse {
  return {
    taskId: response.task_id,
    status: response.status,
    message: response.message,
    estimatedTimeSeconds: response.estimated_time_seconds
  };
}

function mapScore(score: ScoreDetailWire): ScoreDetail {
  return {
    agent: score.agent,
    totalScore: score.total_score,
    maxScore: score.max_score,
    grade: score.grade,
    opinion: score.opinion,
    details: score.details
  };
}

function mapResult(response: AnalysisResultWire): AnalysisResult {
  return {
    taskId: response.task_id,
    status: response.status,
    stock: response.stock,
    mode: response.mode,
    scores: response.scores.map(mapScore),
    finalDecision: response.final_decision,
    researchQuality: response.research_quality,
    qualityWarnings: response.quality_warnings,
    createdAt: response.created_at,
    completedAt: response.completed_at,
    durationSeconds: response.duration_seconds,
    errors: response.errors
  };
}

function mapRealtimePrice(response: RealtimePriceWire): RealtimePrice {
  return {
    stock: response.stock,
    currentPrice: response.current_price,
    change: response.change,
    changeRate: response.change_rate,
    openPrice: response.open_price,
    highPrice: response.high_price,
    lowPrice: response.low_price,
    volume: response.volume,
    marketCap: response.market_cap,
    per: response.per,
    pbr: response.pbr,
    timestamp: response.timestamp
  };
}

function mapCandleHistory(response: CandleHistoryWire): CandleHistory {
  return {
    stockCode: response.stock_code,
    timeframe: response.timeframe,
    candles: response.candles,
    hasMore: response.has_more
  };
}

export function parseProgressEvent(data: string): AnalysisProgressEvent {
  const parsed = JSON.parse(data) as {
    agent: string;
    status: string;
    message: string;
    progress: number;
    timestamp: string;
  };
  return parsed;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store"
  });

  return parseResponse<T>(response);
}

export const authApi = {
  signup: async (payload: SignupRequest) =>
    mapAuthResponse(await api<AuthResponseWire>("/api/v1/auth/signup", {
      method: "POST",
      body: JSON.stringify({
        user_id: payload.userId,
        first_name: payload.firstName,
        last_name: payload.lastName,
        password: payload.password
      })
    })),
  login: async (payload: LoginRequest) =>
    mapAuthResponse(await api<AuthResponseWire>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({
        user_id: payload.userId,
        password: payload.password
      })
    })),
  logout: () =>
    api<AuthResponse>("/api/v1/auth/logout", {
      method: "POST"
    }),
  me: async () => mapAuthUser(await api<AuthUserWire>("/api/v1/auth/me")),
  getPreference: async () => mapPreference(await api<UserPreferenceWire>("/api/v1/auth/me/preference")),
  savePreference: async (payload: UserPreference) =>
    mapPreference(await api<UserPreferenceWire>("/api/v1/auth/me/preference", {
      method: "PUT",
      body: JSON.stringify(toPreferenceWire(payload))
    })),
  getKis: async () => mapKisStatus(await api<KisCredentialsStatusWire>("/api/v1/auth/me/kis")),
  saveKis: async (payload: KisCredentials) =>
    mapKisStatus(await api<KisCredentialsStatusWire>("/api/v1/auth/me/kis", {
      method: "PUT",
      body: JSON.stringify({
        kis_app_key: payload.kisAppKey,
        kis_app_secret: payload.kisAppSecret,
        kis_account_no: payload.kisAccountNo,
        kis_account_product_code: payload.kisAccountProductCode,
        kis_is_real: payload.kisIsReal
      })
    })),
  verifyKis: async (payload: KisCredentials) =>
    mapKisVerification(await api<KisVerificationResultWire>("/api/v1/auth/me/kis/verify", {
      method: "POST",
      body: JSON.stringify({
        kis_app_key: payload.kisAppKey,
        kis_app_secret: payload.kisAppSecret,
        kis_account_no: payload.kisAccountNo,
        kis_account_product_code: payload.kisAccountProductCode,
        kis_is_real: payload.kisIsReal
      })
    }))
};

export const stockApi = {
  search: (query: string) =>
    api<StockSearchResponse>(`/api/v1/stocks/search?q=${encodeURIComponent(query)}`),
  price: async (stockCode: string) =>
    mapRealtimePrice(await api<RealtimePriceWire>(`/api/v1/stocks/${stockCode}/price`))
};

export const chartApi = {
  history: async (stockCode: string, timeframe: string, count = 120, before?: number) => {
    const params = new URLSearchParams({ timeframe, count: String(count) });
    if (before != null) params.set("before", String(before));
    return mapCandleHistory(
      await api<CandleHistoryWire>(`/api/v1/charts/${stockCode}/history?${params.toString()}`)
    );
  }
};

export type BulkAnalysisFailure = {
  stockName: string;
  stockCode: string;
  reason: string;
};

export type BulkAnalysisResponse = {
  total: number;
  submitted: number;
  failed: number;
  tasks: AnalysisTaskResponse[];
  failures: BulkAnalysisFailure[];
};

type BulkAnalysisWire = {
  total: number;
  submitted: number;
  failed: number;
  tasks: AnalysisTaskResponseWire[];
  failures: { stock_name?: string; stockName?: string; stock_code?: string; stockCode?: string; reason: string }[];
};

function mapBulk(wire: BulkAnalysisWire): BulkAnalysisResponse {
  return {
    total: wire.total,
    submitted: wire.submitted,
    failed: wire.failed,
    tasks: wire.tasks.map(mapTask),
    failures: (wire.failures ?? []).map((f) => ({
      stockName: f.stockName ?? f.stock_name ?? "",
      stockCode: f.stockCode ?? f.stock_code ?? "",
      reason: f.reason
    }))
  };
}

export const analysisApi = {
  submit: async (payload: AnalysisRequest) =>
    mapTask(await api<AnalysisTaskResponseWire>("/api/v1/analysis", {
      method: "POST",
      body: JSON.stringify({
        stock_name: payload.stockName,
        stock_code: payload.stockCode,
        mode: payload.mode,
        max_retries: payload.maxRetries
      })
    })),
  bulk: async (mode: "full" | "quick" = "quick", maxRetries = 0) =>
    mapBulk(await api<BulkAnalysisWire>(
      `/api/v1/analysis/bulk?mode=${mode}&maxRetries=${maxRetries}`,
      { method: "POST" }
    )),
  result: async (taskId: string) =>
    mapResult(await api<AnalysisResultWire>(`/api/v1/analysis/${taskId}`))
};

export type AutoTradeStatus = {
  enabled: boolean;
  aiStatus: Record<string, unknown>;
};

export type DirectBuyResult = {
  stockName: string;
  stockCode: string;
  quantity: number;
  limitPrice: number;
  success?: boolean;
  response?: Record<string, unknown>;
  error?: string;
};

type AutoTradeStatusWire = {
  enabled: boolean;
  ai_status?: Record<string, unknown> | null;
  aiStatus?: Record<string, unknown> | null;
};

function mapAutoTradeStatus(wire: AutoTradeStatusWire): AutoTradeStatus {
  return {
    enabled: !!wire.enabled,
    aiStatus: (wire.aiStatus ?? wire.ai_status ?? {}) as Record<string, unknown>
  };
}

export const tradingApi = {
  status: async () =>
    mapAutoTradeStatus(await api<AutoTradeStatusWire>("/api/v1/trading/status")),
  setAuto: async (enabled: boolean) =>
    mapAutoTradeStatus(
      await api<AutoTradeStatusWire>("/api/v1/trading/auto", {
        method: "POST",
        body: JSON.stringify({ enabled })
      })
    ),
  buy: (payload: { stockName: string; stockCode: string; quantity: number; limitPrice: number }) =>
    api<DirectBuyResult>("/api/v1/trading/buy", {
      method: "POST",
      body: JSON.stringify({
        stockName: payload.stockName,
        stockCode: payload.stockCode,
        quantity: payload.quantity,
        limitPrice: payload.limitPrice
      })
    }),
  sell: (payload: { stockName: string; stockCode: string; quantity: number; limitPrice: number }) =>
    api<DirectBuyResult>("/api/v1/trading/sell", {
      method: "POST",
      body: JSON.stringify({
        stockName: payload.stockName,
        stockCode: payload.stockCode,
        quantity: payload.quantity,
        limitPrice: payload.limitPrice
      })
    }),
  orders: (params?: { date?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.date) search.set("date", params.date);
    if (params?.limit) search.set("limit", String(params.limit));
    const qs = search.toString();
    return api<Record<string, unknown>>(`/api/v1/trading/orders${qs ? `?${qs}` : ""}`);
  }
};

export const chatApi = {
  send: (message: string, sessionId?: string) =>
    api<Record<string, unknown>>("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify({ message, sessionId })
    })
};

export function eventStreamUrl(path: string) {
  return `${API_BASE}${path}`;
}
