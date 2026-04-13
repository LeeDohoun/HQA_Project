import type {
  AnalysisRequest,
  AnalysisResult,
  AnalysisTaskResponse,
  AnalysisProgressEvent,
  ApiError,
  AuthResponse,
  AuthUser,
  CandleHistory,
  LoginRequest,
  RealtimePrice,
  ScoreDetail,
  SignupRequest,
  StockInfo,
  StockSearchResponse,
  UserPreference
} from "@/types/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

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
    }))
};

export const stockApi = {
  search: (query: string) =>
    api<StockSearchResponse>(`/api/v1/stocks/search?q=${encodeURIComponent(query)}`),
  price: async (stockCode: string) =>
    mapRealtimePrice(await api<RealtimePriceWire>(`/api/v1/stocks/${stockCode}/price`))
};

export const chartApi = {
  history: async (stockCode: string, timeframe: string, count = 120) =>
    mapCandleHistory(
      await api<CandleHistoryWire>(
        `/api/v1/charts/${stockCode}/history?timeframe=${encodeURIComponent(timeframe)}&count=${count}`
      )
    )
};

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
  result: async (taskId: string) =>
    mapResult(await api<AnalysisResultWire>(`/api/v1/analysis/${taskId}`))
};

export function eventStreamUrl(path: string) {
  return `${API_BASE}${path}`;
}
