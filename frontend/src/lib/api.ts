// 파일: frontend/src/lib/api.ts
/**
 * HQA API 클라이언트
 * 
 * FastAPI 백엔드와 통신하는 함수들을 정의합니다.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ── 타입 정의 ──

export interface StockInfo {
  name: string;
  code: string;
}

export interface StockSearchResult {
  results: StockInfo[];
  total: number;
}

export interface RealtimePrice {
  stock: StockInfo;
  current_price: number;
  change: number;
  change_rate: number;
  open_price: number;
  high_price: number;
  low_price: number;
  volume: number;
  market_cap?: number;
  per?: number;
  pbr?: number;
  timestamp: string;
}

export interface AnalysisTask {
  task_id: string;
  status: string;
  message: string;
  estimated_time_seconds: number;
}

export interface ScoreDetail {
  agent: string;
  total_score: number;
  max_score: number;
  grade?: string;
  opinion?: string;
  details: Record<string, any>;
}

export interface AnalysisResult {
  task_id: string;
  status: string;
  stock: StockInfo;
  mode: string;
  scores: ScoreDetail[];
  final_decision?: Record<string, any>;
  research_quality?: string;
  quality_warnings: string[];
  created_at: string;
  completed_at?: string;
  duration_seconds?: number;
}

export interface AgentProgress {
  agent: string;
  status: string;
  message: string;
  progress: number;
  timestamp: string;
}

export interface ChatResponse {
  session_id: string;
  message: string;
  intent?: string;
  stocks: StockInfo[];
  analysis_triggered: boolean;
  task_id?: string;
}

export interface QuerySuggestion {
  original_query: string;
  is_answerable: boolean;
  corrected_query?: string;
  suggestions: string[];
  reason?: string;
}

// ── 차트 관련 타입 ──

export interface CandleData {
  time: number;       // UNIX timestamp (초)
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  complete?: boolean;
}

interface CandleHistoryResponse {
  stock_code: string;
  timeframe: string;
  candles: CandleData[];
  has_more: boolean;
}

// ── 에러 타입 ──

export interface ApiError {
  success: false;
  error_code: string;
  message: string;
  detail?: string;
}

export class ApiRequestError extends Error {
  public errorCode: string;
  public statusCode: number;
  public detail?: string;

  constructor(statusCode: number, apiError: ApiError) {
    super(apiError.message);
    this.name = 'ApiRequestError';
    this.statusCode = statusCode;
    this.errorCode = apiError.error_code;
    this.detail = apiError.detail;
  }
}

// ── API 함수 ──

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    // 표준 에러 응답 포맷 감지: { success: false, error_code: "...", message: "..." }
    if (body && body.detail && typeof body.detail === 'object' && body.detail.error_code) {
      throw new ApiRequestError(res.status, body.detail as ApiError);
    }
    // 레거시 에러 포맷
    const msg = body?.detail || body?.message || res.statusText;
    throw new Error(typeof msg === 'string' ? msg : `API Error: ${res.status}`);
  }
  return res.json();
}

/** 종목 검색 */
export async function searchStocks(query: string): Promise<StockSearchResult> {
  return apiFetch(`/api/v1/stocks/search?q=${encodeURIComponent(query)}`);
}

/** 실시간 시세 */
export async function getRealtimePrice(stockCode: string): Promise<RealtimePrice> {
  return apiFetch(`/api/v1/stocks/${stockCode}/price`);
}

/** 분석 요청 */
export async function requestAnalysis(
  stockName: string,
  stockCode: string,
  mode: 'full' | 'quick' = 'full',
): Promise<AnalysisTask> {
  return apiFetch('/api/v1/analysis', {
    method: 'POST',
    body: JSON.stringify({
      stock_name: stockName,
      stock_code: stockCode,
      mode,
    }),
  });
}

/** 분석 결과 조회 */
export async function getAnalysisResult(taskId: string): Promise<AnalysisResult> {
  return apiFetch(`/api/v1/analysis/${taskId}`);
}

/** SSE 스트리밍 (진행 상황 실시간 수신) */
export function streamAnalysisProgress(
  taskId: string,
  onProgress: (event: AgentProgress) => void,
  onComplete: () => void,
  onError: (error: string) => void,
): EventSource {
  const eventSource = new EventSource(`${API_BASE}/api/v1/analysis/${taskId}/stream`);

  eventSource.addEventListener('progress', (event) => {
    const data = JSON.parse((event as MessageEvent).data);
    onProgress(data);
  });

  eventSource.addEventListener('completed', () => {
    onComplete();
    eventSource.close();
  });

  eventSource.addEventListener('error', (event) => {
    const data = (event as MessageEvent).data;
    onError(data ? JSON.parse(data).error : 'Connection lost');
    eventSource.close();
  });

  eventSource.onerror = () => {
    onError('SSE connection error');
    eventSource.close();
  };

  return eventSource;
}

/** 대화 */
export async function chat(
  message: string,
  sessionId?: string,
): Promise<ChatResponse> {
  return apiFetch('/api/v1/analysis/chat', {
    method: 'POST',
    body: JSON.stringify({ message, session_id: sessionId }),
  });
}

/** 쿼리 제안 */
export async function suggestQuery(query: string): Promise<QuerySuggestion> {
  return apiFetch('/api/v1/analysis/suggest', {
    method: 'POST',
    body: JSON.stringify({ query }),
  });
}

/** 과거 캔들 데이터 조회 - KIS API (페이지네이션 지원) */
export async function getHistoricalCandles(
  stockCode: string,
  timeframe: string,
  count: number = 200,
  before?: number,
): Promise<{ candles: CandleData[]; hasMore: boolean }> {
  let url = `/api/v1/charts/${stockCode}/history?timeframe=${timeframe}&count=${count}`;
  if (before !== undefined) {
    url += `&before=${before}`;
  }
  const resp = await apiFetch<CandleHistoryResponse>(url);
  return { candles: resp.candles, hasMore: resp.has_more };
}
