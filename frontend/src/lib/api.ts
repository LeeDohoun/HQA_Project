import type {
  MyProfile, BoardType, InquiryStatus, PostAuthor, PostComment, CommentPage, PostDetail, PostListResult, PostSummary,
  AnalysisHistoryItem,
  AnalysisHistoryResponse,
  AnalysisProgressPollResponse,
  AnalysisRequest,
  AnalysisResult,
  AnalysisTaskResponse,
  AiActivityResponse,
  AutoTradeExplanationResponse,
  ApiError,
  AuthResponse,
  AuthUser,
  Balance,
  CandleHistory,
  DisclosureItem,
  KisCredentials,
  KisCredentialsStatus,
  KisVerificationResult,
  LoginRequest,
  MarketIndexResponse,
  NewsItem,
  RealtimePrice,
  ScoreDetail,
  SignupRequest,
  StockInfo,
  StockSearchResponse,
  UserPreference
} from "@/types/api";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");
const isLocalApiBase = API_BASE.startsWith("http://localhost") || API_BASE.startsWith("http://127.0.0.1");

if (process.env.NODE_ENV === "production" && API_BASE.startsWith("http://") && !isLocalApiBase) {
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
  if (response.status === 204 || response.status === 205) return undefined as T;
  const raw = await response.text();
  let body: unknown = raw;
  if (contentType.includes("json") && raw) {
    try { body = JSON.parse(raw); }
    catch {
      const error = new Error("서버 응답을 읽을 수 없습니다. 잠시 후 다시 시도해 주세요.") as ApiError;
      error.status = response.status;
      throw error;
    }
  }

  if (!response.ok) {
    const message = typeof body === "object" && body !== null
      ? extractErrorMessage(body)
      : `요청을 처리하지 못했습니다 (${response.status}). 잠시 후 다시 시도해 주세요.`;
    const error = new Error(message === "PAPER_ACCOUNT_REQUIRED"
      ? "자동매매와 주문은 모의투자 계좌에서만 사용할 수 있어요."
      : message) as ApiError;
    error.status = response.status;
    error.payload = body;
    throw error;
  }

  return body as T;
}

type AuthUserWire = {
  id: string;
  user_id: string;
  nickname: string;
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
  estimated_time_seconds: number | null;
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

type AnalysisProgressPollWire = {
  task_id?: string;
  taskId?: string;
  status: AnalysisProgressPollResponse["status"];
  events?: { type: string; data: Record<string, unknown> }[];
};

type AnalysisHistoryItemWire = {
  task_id: string;
  stock: StockInfo;
  mode: AnalysisHistoryItem["mode"];
  status: AnalysisHistoryItem["status"];
  total_score: number | null;
  action: string | null;
  created_at: string;
  completed_at: string | null;
};

type AnalysisHistoryResponseWire = {
  items: AnalysisHistoryItemWire[];
  total: number;
  page: number;
  page_size: number;
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
    nickname: user.nickname ?? user.user_id,
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

function mapHistoryItem(item: AnalysisHistoryItemWire): AnalysisHistoryItem {
  return {
    taskId: item.task_id,
    stock: item.stock,
    mode: item.mode,
    status: item.status,
    totalScore: item.total_score,
    action: item.action,
    createdAt: item.created_at,
    completedAt: item.completed_at
  };
}

function mapHistory(response: AnalysisHistoryResponseWire): AnalysisHistoryResponse {
  return {
    items: response.items.map(mapHistoryItem),
    total: response.total,
    page: response.page,
    pageSize: response.page_size
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

function mapProgressPoll(wire: AnalysisProgressPollWire): AnalysisProgressPollResponse {
  return {
    taskId: wire.taskId ?? wire.task_id ?? "",
    status: wire.status,
    events: wire.events ?? []
  };
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json");
  }

  const controller = new AbortController();
  const abort = () => controller.abort(init?.signal?.reason);
  if (init?.signal?.aborted) abort();
  else init?.signal?.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(() => controller.abort(new DOMException("Request timed out", "TimeoutError")), 45_000);
  try {
    const method = (init?.method ?? "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const csrfResponse = await fetch(`${API_BASE}/api/v1/auth/csrf`, {
        credentials: "include", cache: "no-store", signal: controller.signal
      });
      const csrf = await parseResponse<{ token: string }>(csrfResponse);
      if (!csrf?.token) throw new Error("Could not verify your session. Please retry.");
      headers.set("X-CSRF-Token", csrf.token);
    }
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers,
      credentials: "include",
      cache: "no-store"
    });
    return await parseResponse<T>(response);
  } catch (error) {
    if (controller.signal.aborted && !init?.signal?.aborted) {
      throw new Error("응답 시간이 길어지고 있습니다. 분석·주문 내역을 확인한 뒤 다시 시도해 주세요.");
    }
    if (error instanceof TypeError) {
      throw new Error("서버에 연결하지 못했습니다. 연결 상태를 확인해 주세요.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", abort);
  }
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
    mapRealtimePrice(await api<RealtimePriceWire>(`/api/v1/stocks/${stockCode}/price`)),
  news: (stockCode: string, limit = 20) =>
    api<{ items: NewsItem[]; error?: string }>(`/api/v1/stocks/${stockCode}/news?limit=${limit}`),
  disclosures: (stockCode: string, limit = 20) =>
    api<{ items: DisclosureItem[]; error?: string }>(`/api/v1/stocks/${stockCode}/disclosures?limit=${limit}`),
  indices: () => api<MarketIndexResponse>("/api/v1/stocks/indices")
};

type WatchlistItemWire = {
  id: string;
  stock_name?: string;
  stockName?: string;
  stock_code?: string;
  stockCode?: string;
  market: string;
  created_at?: string;
  createdAt?: string;
  updated_at?: string;
  updatedAt?: string;
};

type WatchlistWire = {
  items: WatchlistItemWire[];
  total: number;
};

function mapWatchlistItem(wire: WatchlistItemWire) {
  return {
    id: wire.id,
    name: wire.stockName ?? wire.stock_name ?? "",
    code: wire.stockCode ?? wire.stock_code ?? "",
    market: wire.market,
    createdAt: wire.createdAt ?? wire.created_at,
    updatedAt: wire.updatedAt ?? wire.updated_at
  };
}

export const watchlistApi = {
  list: async () => {
    const wire = await api<WatchlistWire>("/api/v1/watchlist");
    return {
      total: wire.total,
      items: wire.items.map(mapWatchlistItem)
    };
  },
  add: async (stock: { name: string; code: string; market: string }) =>
    mapWatchlistItem(await api<WatchlistItemWire>("/api/v1/watchlist", {
      method: "POST",
      body: JSON.stringify({
        stock_name: stock.name,
        stock_code: stock.code,
        market: stock.market
      })
    })),
  remove: (stockCode: string) =>
    api<void>(`/api/v1/watchlist/${encodeURIComponent(stockCode)}`, {
      method: "DELETE"
    })
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

export type BulkAnalysisItem = {
  stockName: string;
  stockCode: string;
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
  bulk: async (mode: "full" | "quick" = "full", maxRetries = 0, items?: BulkAnalysisItem[]) =>
    mapBulk(await api<BulkAnalysisWire>(
      `/api/v1/analysis/bulk?mode=${mode}&maxRetries=${maxRetries}`,
      {
        method: "POST",
        body: items !== undefined ? JSON.stringify({ items }) : undefined
      }
    )),
  result: async (taskId: string) =>
    mapResult(await api<AnalysisResultWire>(`/api/v1/analysis/${taskId}`)),
  progress: async (taskId: string) =>
    mapProgressPoll(await api<AnalysisProgressPollWire>(`/api/v1/analysis/${taskId}/progress`)),
  history: async (page = 1, pageSize = 10) =>
    mapHistory(await api<AnalysisHistoryResponseWire>(`/api/v1/analysis/history/list?page=${page}&pageSize=${pageSize}`))
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
        stock_name: payload.stockName,
        stock_code: payload.stockCode,
        quantity: payload.quantity,
        limit_price: payload.limitPrice
      })
    }),
  sell: (payload: { stockName: string; stockCode: string; quantity: number; limitPrice: number }) =>
    api<DirectBuyResult>("/api/v1/trading/sell", {
      method: "POST",
      body: JSON.stringify({
        stock_name: payload.stockName,
        stock_code: payload.stockCode,
        quantity: payload.quantity,
        limit_price: payload.limitPrice
      })
    }),
  orders: (params?: { date?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.date) search.set("date", params.date);
    if (params?.limit) search.set("limit", String(params.limit));
    const qs = search.toString();
    return api<Record<string, unknown>>(`/api/v1/trading/orders${qs ? `?${qs}` : ""}`);
  },
  balance: () => api<Balance>("/api/v1/trading/balance"),
  aiActivity: (limit = 6) => api<AiActivityResponse>(`/api/v1/trading/ai-activity?limit=${limit}`),
  explanations: (limit = 6) => api<AutoTradeExplanationResponse>(`/api/v1/trading/explanations?limit=${limit}`)
};

export const chatApi = {
  send: (message: string, sessionId?: string) =>
    api<Record<string, unknown>>("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId })
    })
};

export function eventStreamUrl(path: string) {
  return `${API_BASE}${path}`;
}

type PostAuthorWire = {
  id: string;
  user_id: string;
  first_name: string;
  last_name: string;
  nickname: string;
  level: number;
  title: string | null;
};

type PostSummaryWire = {
  id: string;
  board_type: BoardType;
  title: string;
  stock_code: string | null;
  comment_count: number;
  recommendation_count: number;
  author_nickname: string;
  author_level: number;
  author_title: string | null;
  inquiry_status: InquiryStatus | null;
  author_id: string;
  author_user_id: string;
  author_first_name: string;
  author_last_name: string;
  created_at: string;
  updated_at: string;
};

type PostCommentWire = {
  id: string;
  content: string;
  author: PostAuthorWire;
  mine: boolean;
  deletable: boolean;
  created_at: string;
  updated_at: string;
};

type CommentPageWire = {
  items: PostCommentWire[];
  next_cursor: string | null;
  has_more: boolean;
  total_items: number;
};

type PostDetailWire = {
  id: string;
  board_type: BoardType;
  title: string;
  content: string;
  stock_code: string | null;
  comment_count: number;
  recommendation_count: number;
  recommended: boolean;
  recommendable: boolean;
  author: PostAuthorWire;
  deletable: boolean;
  editable: boolean;
  delete_requestable: boolean;
  inquiry_resolvable: boolean;
  target_post_deletable: boolean;
  delete_blocked_reason: string | null;
  inquiry_status: InquiryStatus | null;
  admin_reply: string | null;
  target_post_id: string | null;
  comments: PostCommentWire[];
  next_comment_cursor: string | null;
  has_more_comments: boolean;
  created_at: string;
  updated_at: string;
};

type PostListWire = {
  items: PostSummaryWire[];
  page: number;
  size: number;
  total_items: number;
  total_pages: number;
};

function mapAuthor(wire: PostAuthorWire): PostAuthor {
  return {
    id: wire.id,
    userId: wire.user_id,
    firstName: wire.first_name,
    lastName: wire.last_name,
    nickname: wire.nickname ?? wire.user_id,
    level: wire.level ?? 0,
    title: wire.title ?? null
  };
}

function mapPostSummary(wire: PostSummaryWire): PostSummary {
  return {
    id: wire.id,
    boardType: wire.board_type,
    title: wire.title,
    stockCode: wire.stock_code,
    commentCount: wire.comment_count,
    recommendationCount: wire.recommendation_count ?? 0,
    inquiryStatus: wire.inquiry_status,
    author: {
      id: wire.author_id,
      userId: wire.author_user_id,
      firstName: wire.author_first_name,
      lastName: wire.author_last_name,
      nickname: wire.author_nickname ?? wire.author_user_id,
      level: wire.author_level ?? 0,
      title: wire.author_title ?? null
    },
    createdAt: wire.created_at,
    updatedAt: wire.updated_at
  };
}

function mapComment(wire: PostCommentWire): PostComment {
  return {
    id: wire.id,
    content: wire.content,
    author: mapAuthor(wire.author),
    mine: wire.mine,
    deletable: wire.deletable,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at
  };
}

function mapPostDetail(wire: PostDetailWire): PostDetail {
  return {
    id: wire.id,
    boardType: wire.board_type,
    title: wire.title,
    content: wire.content,
    stockCode: wire.stock_code,
    commentCount: wire.comment_count,
    recommendationCount: wire.recommendation_count ?? 0,
    recommended: wire.recommended ?? false,
    recommendable: wire.recommendable ?? false,
    author: mapAuthor(wire.author),
    deletable: wire.deletable,
    editable: wire.editable,
    deleteRequestable: wire.delete_requestable,
    inquiryResolvable: wire.inquiry_resolvable,
    targetPostDeletable: wire.target_post_deletable,
    deleteBlockedReason: wire.delete_blocked_reason,
    inquiryStatus: wire.inquiry_status,
    adminReply: wire.admin_reply,
    targetPostId: wire.target_post_id,
    comments: (wire.comments ?? []).map(mapComment),
    nextCommentCursor: wire.next_comment_cursor,
    hasMoreComments: wire.has_more_comments,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at
  };
}

function mapPostList(wire: PostListWire): PostListResult {
  return {
    items: (wire.items ?? []).map(mapPostSummary),
    page: wire.page,
    size: wire.size,
    totalItems: wire.total_items,
    totalPages: wire.total_pages
  };
}

function pageQuery(page: number, size: number, extra?: Record<string, string>) {
  const params = new URLSearchParams({ page: String(page), size: String(size), ...(extra ?? {}) });
  return params.toString();
}

export const communityApi = {
  recommend: async (postId: string) =>
    mapPostDetail(await api<PostDetailWire>(`/api/v1/community/posts/${encodeURIComponent(postId)}/recommendations`, { method: "POST" })),
  listFree: async (page = 0, size = 20, signal?: AbortSignal) =>
    mapPostList(await api<PostListWire>(`/api/v1/community/free?${pageQuery(page, size)}`, { signal })),

  listStock: async (stockCode?: string, page = 0, size = 20, signal?: AbortSignal) =>
    mapPostList(await api<PostListWire>(
      `/api/v1/community/stock?${pageQuery(page, size, stockCode ? { stockCode } : undefined)}`,
      { signal }
    )),

  listInquiries: async (page = 0, size = 20, signal?: AbortSignal) =>
    mapPostList(await api<PostListWire>(`/api/v1/community/inquiries?${pageQuery(page, size)}`, { signal })),

  get: async (postId: string, signal?: AbortSignal) =>
    mapPostDetail(await api<PostDetailWire>(`/api/v1/community/posts/${encodeURIComponent(postId)}`, { signal })),

  listComments: async (postId: string, after?: string | null, size = 20, signal?: AbortSignal): Promise<CommentPage> => {
    const params = new URLSearchParams({ size: String(size) });
    if (after) params.set("after", after);
    const wire = await api<CommentPageWire>(`/api/v1/community/posts/${encodeURIComponent(postId)}/comments?${params}`, { signal });
    return { items: wire.items.map(mapComment), nextCursor: wire.next_cursor, hasMore: wire.has_more, totalItems: wire.total_items };
  },

  visibleComments: async (postId: string, ids: string[], signal?: AbortSignal): Promise<CommentPage> => {
    const params = new URLSearchParams();
    for (const id of ids) params.append("ids", id);
    const wire = await api<CommentPageWire>(`/api/v1/community/posts/${encodeURIComponent(postId)}/comments/visible?${params}`, { signal });
    return { items: wire.items.map(mapComment), nextCursor: wire.next_cursor, hasMore: wire.has_more, totalItems: wire.total_items };
  },

  createFree: async (payload: { title: string; content: string }) =>
    mapPostDetail(await api<PostDetailWire>("/api/v1/community/free", {
      method: "POST",
      body: JSON.stringify({ title: payload.title, content: payload.content })
    })),

  createStock: async (payload: { title: string; content: string; stockCode: string }) =>
    mapPostDetail(await api<PostDetailWire>("/api/v1/community/stock", {
      method: "POST",
      body: JSON.stringify({ title: payload.title, content: payload.content, stock_code: payload.stockCode })
    })),

  createInquiry: async (payload: { title: string; content: string; targetPostId?: string }) =>
    mapPostDetail(await api<PostDetailWire>("/api/v1/community/inquiries", {
      method: "POST",
      body: JSON.stringify({
        title: payload.title,
        content: payload.content,
        target_post_id: payload.targetPostId ?? null
      })
    })),

  update: async (postId: string, payload: { title: string; content: string }) =>
    mapPostDetail(await api<PostDetailWire>(`/api/v1/community/posts/${encodeURIComponent(postId)}`, {
      method: "PUT",
      body: JSON.stringify({ title: payload.title, content: payload.content })
    })),

  remove: (postId: string) =>
    api<void>(`/api/v1/community/posts/${encodeURIComponent(postId)}`, { method: "DELETE" }),

  addComment: async (postId: string, content: string) =>
    mapComment(await api<PostCommentWire>(`/api/v1/community/posts/${encodeURIComponent(postId)}/comments`, {
      method: "POST",
      body: JSON.stringify({ content })
    })),

  removeComment: (commentId: string) =>
    api<void>(`/api/v1/community/comments/${encodeURIComponent(commentId)}`, { method: "DELETE" }),

  /** 관리자 전용. 삭제 요청이면 deleteTargetPost로 대상 글을 함께 지운다. */
  resolveInquiry: async (
    inquiryId: string,
    payload: { status: Exclude<InquiryStatus, "OPEN">; adminReply?: string; deleteTargetPost?: boolean }
  ) =>
    mapPostDetail(await api<PostDetailWire>(`/api/v1/community/inquiries/${encodeURIComponent(inquiryId)}`, {
      method: "PATCH",
      body: JSON.stringify({
        status: payload.status,
        admin_reply: payload.adminReply ?? null,
        delete_target_post: payload.deleteTargetPost ?? false
      })
    }))
};

type MyProfileWire = {
  id: string; user_id: string; nickname: string; total_points: number; created_at: string;
  boards: { board_type: "FREE" | "STOCK"; points: number; level: number; title: string;
    current_level_points: number; next_level_points: number | null; points_to_next_level: number;
    post_count: number; comment_count: number; recommendations_received: number }[];
  recent_events: { id: string; board_type: "FREE" | "STOCK"; reason: "POST_CREATED" | "COMMENT_CREATED" | "RECOMMEND_RECEIVED";
    points: number; post_id: string; created_at: string; reversed_at: string | null }[];
};

function mapProfile(wire: MyProfileWire): MyProfile {
  return { id: wire.id, userId: wire.user_id, nickname: wire.nickname, totalPoints: wire.total_points, createdAt: wire.created_at,
    boards: wire.boards.map(board => ({ boardType: board.board_type, points: board.points, level: board.level,
      title: board.title, currentLevelPoints: board.current_level_points, nextLevelPoints: board.next_level_points,
      pointsToNextLevel: board.points_to_next_level, postCount: board.post_count, commentCount: board.comment_count,
      recommendationsReceived: board.recommendations_received })),
    recentEvents: wire.recent_events.map(event => ({ id: event.id, boardType: event.board_type, reason: event.reason,
      points: event.points, postId: event.post_id, createdAt: event.created_at, reversedAt: event.reversed_at })) };
}

export const profileApi = {
  me: async (signal?: AbortSignal) => mapProfile(await api<MyProfileWire>("/api/v1/profile", { signal })),
  updateNickname: async (nickname: string) => mapProfile(await api<MyProfileWire>("/api/v1/profile/nickname", {
    method: "PUT", body: JSON.stringify({ nickname })
  }))
};
