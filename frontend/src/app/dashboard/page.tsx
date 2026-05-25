"use client";

/* ============================================================
   대시보드 — 워치리스트 / AI 분석 / 거래 내역 / 내 자산 4탭
   디자인은 /prototype 의 에디토리얼 톤(다크). 기능·API 로직은 기존과 동일.
   ============================================================ */

import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { analysisApi, authApi, eventStreamUrl, stockApi, tradingApi } from "@/lib/api";
import { titleCaseAgent } from "@/lib/format";
import type {
  AnalysisHistoryItem,
  AnalysisMode,
  AnalysisProgressEvent,
  AnalysisResult,
  AnalysisTaskResponse,
  AuthUser,
  Balance,
  MarketIndex,
  StockSearchResult,
  UserPreference
} from "@/types/api";

/* ============================================================
   디자인 시스템 (에디토리얼 · 다크)
   ============================================================ */
const CSS = `
.ed{
  --paper:#14130d; --paper-2:#1d1b12; --ink:#ece6d3; --ink-2:#a39c84; --ink-3:#6d6753;
  --card:#1f1c12; --forest:#1c5040; --forest-ink:#e9e4cf;
  --moss:#36b079; --moss-2:#43c489; --spark:#e0a341;
  --rule:#322d1f;
  --up:#d2554a; --down:#5d83d6;
  --serif:Georgia,"Times New Roman",serif;
  --sans:-apple-system,BlinkMacSystemFont,"Pretendard","Apple SD Gothic Neo","Noto Sans KR","Segoe UI",sans-serif;
  --ease:cubic-bezier(.22,1,.36,1);
  background:var(--paper); color:var(--ink); font-family:var(--sans);
  font-size:16px; line-height:1.6; -webkit-font-smoothing:antialiased; min-height:100vh;
}
.ed *{box-sizing:border-box;}
.ed-up{color:var(--up);} .ed-down{color:var(--down);}
.ed-tnum{font-variant-numeric:tabular-nums;}
.ed-serif{font-family:var(--serif);}

.ed-wrap{max-width:1180px; margin:0 auto; padding:0 clamp(18px,4vw,52px);}
.ed-rule{height:1px; background:var(--rule); border:0; margin:0;}
.ed-fine{font-size:.78rem; color:var(--ink-3); line-height:1.7;}
.ed-hint{color:var(--ink-3); font-size:.92rem; line-height:1.6;}
.ed-label{font-size:.74rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; color:var(--moss);}
.ed-eyebrow{display:inline-flex; align-items:center; gap:8px; font-size:.78rem; font-weight:800; letter-spacing:.04em; color:var(--ink-2);}
.ed-dot{width:7px; height:7px; border-radius:50%; background:var(--ink-3);}
.ed-dot--live{background:var(--moss); animation:ed-pulse 2s infinite;}
@keyframes ed-pulse{0%{box-shadow:0 0 0 0 rgba(54,176,121,.5);}70%{box-shadow:0 0 0 8px rgba(54,176,121,0);}100%{box-shadow:0 0 0 0 rgba(54,176,121,0);}}

/* 버튼 */
.ed-btn{
  display:inline-flex; align-items:center; justify-content:center; gap:8px; cursor:pointer;
  font-family:var(--sans); font-size:.94rem; font-weight:800; letter-spacing:-.01em;
  padding:12px 20px; border:1px solid transparent; border-radius:5px; white-space:nowrap;
  transition:transform .14s var(--ease),background .15s,opacity .15s;
}
.ed-btn:active{transform:translateY(1px);}
.ed-btn:disabled{opacity:.4; cursor:not-allowed;}
.ed-btn--moss{background:var(--moss); color:#0b2417;}
.ed-btn--moss:hover:not(:disabled){background:var(--moss-2);}
.ed-btn--ink{background:var(--ink); color:var(--paper);}
.ed-btn--line{background:transparent; color:var(--ink); border-color:var(--rule);}
.ed-btn--line:hover:not(:disabled){border-color:var(--ink-2);}
.ed-btn--buy{background:var(--up); color:#fff;}
.ed-btn--sell{background:var(--down); color:#fff;}
.ed-btn--block{width:100%;}
.ed-btn--sm{padding:9px 14px; font-size:.86rem;}
.ed-tlink{background:none; border:none; cursor:pointer; padding:0; font:inherit; color:var(--ink-2); font-weight:800; text-decoration:underline; text-underline-offset:4px;}
.ed-tlink:hover{color:var(--ink);}

/* 네비 */
.ed-nav{position:sticky; top:0; z-index:40; background:rgba(20,19,13,.92); backdrop-filter:saturate(150%) blur(12px); -webkit-backdrop-filter:saturate(150%) blur(12px); border-bottom:1px solid var(--rule);}
.ed-nav-in{max-width:1180px; margin:0 auto; padding:0 clamp(18px,4vw,52px); height:64px; display:flex; align-items:center; gap:10px;}
.ed-mark{display:inline-flex; align-items:baseline;}
.ed-mark b{font-family:var(--serif); font-style:italic; font-weight:700; font-size:1.42rem; letter-spacing:-.02em;}
.ed-mark i{width:6px; height:6px; border-radius:50%; background:var(--moss); margin-left:3px; align-self:flex-end; margin-bottom:5px;}
.ed-nav-links{display:flex; gap:2px; margin-left:20px; flex-wrap:wrap;}
.ed-nav-link{
  background:none; border:none; cursor:pointer; font-family:var(--sans);
  font-size:.9rem; font-weight:700; color:var(--ink-3); padding:8px 11px;
  border-bottom:2px solid transparent; transition:color .14s,border-color .14s;
}
.ed-nav-link:hover{color:var(--ink);}
.ed-nav-link--on{color:var(--ink); border-bottom-color:var(--moss);}
.ed-nav-right{margin-left:auto; display:flex; align-items:center; gap:10px;}
.ed-statuschip{
  display:inline-flex; align-items:center; gap:7px; height:36px; padding:0 13px;
  border:1px solid var(--rule); border-radius:5px; cursor:pointer; background:transparent;
  font-size:.79rem; font-weight:800; color:var(--ink-2);
}
.ed-statuschip:hover{border-color:var(--ink-3);}
.ed-statuschip--on{border-color:var(--moss); color:var(--moss);}

/* 앱 본문 */
.ed-app{padding:clamp(24px,4vw,44px) 0 110px;}
.ed-app-head{margin-bottom:20px;}
.ed-kicker{font-family:var(--serif); font-style:italic; font-size:1.1rem; color:var(--ink-3);}
.ed-app-h{font-size:clamp(1.5rem,3vw,2.2rem); font-weight:800; letter-spacing:-.03em; margin:3px 0 0;}
.ed-greet{font-family:var(--serif); font-style:italic; color:var(--ink-3); font-size:.95rem;}

/* 섹션 */
.ed-sec{margin-top:34px;}
.ed-sec-head{display:flex; align-items:baseline; justify-content:space-between; gap:12px; padding-bottom:11px; border-bottom:1.5px solid var(--ink); margin-bottom:2px;}
.ed-sec-title{font-size:1.1rem; font-weight:800; letter-spacing:-.02em;}
.ed-sec-meta{font-size:.8rem; color:var(--ink-3); font-weight:700;}

/* 큰 숫자 */
.ed-figrow{display:flex; gap:clamp(22px,5vw,60px); flex-wrap:wrap;}
.ed-fig small{font-size:.78rem; font-weight:700; color:var(--ink-2); letter-spacing:.02em;}
.ed-fig b{display:block; font-family:var(--serif); font-weight:700; letter-spacing:-.02em; line-height:1.1; margin-top:5px;}
.ed-fig--xl b{font-size:clamp(2rem,4.5vw,3rem);}
.ed-fig--md b{font-size:clamp(1.4rem,2.6vw,1.9rem);}
.ed-fig-delta{font-size:.9rem; font-weight:800; margin-top:5px;}

/* 리스트 행 */
.ed-list{display:flex; flex-direction:column;}
.ed-row{
  display:flex; align-items:center; gap:14px; padding:14px 4px; width:100%; text-align:left;
  border:0; border-bottom:1px solid var(--rule); background:none; color:inherit; cursor:pointer;
  transition:background .12s;
}
.ed-row:hover{background:var(--card);}
.ed-row--static{cursor:default;}
.ed-row--static:hover{background:none;}
.ed-row--on{background:var(--card);}
.ed-row:last-child{border-bottom:0;}
.ed-row-mk{
  width:40px; height:40px; flex-shrink:0; border:1px solid var(--rule); background:var(--paper-2);
  display:inline-flex; align-items:center; justify-content:center; font-family:var(--serif);
  font-weight:700; font-size:.92rem; color:var(--ink-2);
}
.ed-row-main{flex:1; min-width:0;}
.ed-row-name{font-weight:800; font-size:.98rem;}
.ed-row-meta{font-size:.8rem; color:var(--ink-3); font-weight:600; margin-top:1px; font-variant-numeric:tabular-nums;}
.ed-row-num{text-align:right; flex-shrink:0;}
.ed-row-val{font-family:var(--serif); font-size:1.05rem; font-weight:700;}
.ed-row-pl{font-size:.8rem; font-weight:800; margin-top:1px; font-variant-numeric:tabular-nums;}

/* 폼 */
.ed-field{display:flex; flex-direction:column; gap:7px;}
.ed-flabel{font-size:.78rem; font-weight:800; color:var(--ink-2); letter-spacing:.02em;}
.ed-input{
  width:100%; background:var(--card); border:1px solid var(--rule); color:var(--ink);
  font-family:var(--sans); font-size:.95rem; padding:11px 13px; border-radius:5px; outline:none;
  transition:border-color .14s;
}
.ed-input:focus{border-color:var(--moss);}
.ed-input::placeholder{color:var(--ink-3);}
.ed-searchbar{display:flex; gap:8px;}
.ed-searchbar .ed-input{flex:1;}
.ed-seg{display:inline-flex; border:1px solid var(--rule); border-radius:5px; overflow:hidden;}
.ed-seg-btn{
  background:none; border:0; cursor:pointer; font:inherit; font-weight:800; font-size:.86rem;
  color:var(--ink-3); padding:9px 16px;
}
.ed-seg-btn + .ed-seg-btn{border-left:1px solid var(--rule);}
.ed-seg-btn--on{background:var(--ink); color:var(--paper);}
.ed-seg-btn--buy.ed-seg-btn--on{background:var(--up); color:#fff;}
.ed-seg-btn--sell.ed-seg-btn--on{background:var(--down); color:#fff;}

/* 차트 / 시세 */
.ed-pricebar{display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin:8px 0 0;}
.ed-price-now{font-family:var(--serif); font-size:2rem; font-weight:700;}
.ed-price-d{font-size:1rem; font-weight:800; font-variant-numeric:tabular-nums;}
.ed-chart-frame{height:380px; border:1px solid var(--rule); background:var(--card); padding:8px; margin-top:14px;}
.ed-chart-empty{height:100%; display:flex; align-items:center; justify-content:center; color:var(--ink-3); font-size:.9rem;}
.ed-quotegrid{display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--rule); border:1px solid var(--rule); margin-top:14px;}
.ed-quote-cell{background:var(--card); padding:11px 13px;}
.ed-quote-cell small{display:block; font-size:.72rem; color:var(--ink-3); font-weight:700;}
.ed-quote-cell b{font-family:var(--serif); font-size:1.02rem; font-weight:700;}

/* 태그 / 진행 / 메시지 */
.ed-tag{display:inline-flex; align-items:center; font-size:.71rem; font-weight:800; padding:3px 9px; border-radius:3px;}
.ed-tag--good{background:rgba(54,176,121,.18); color:var(--moss);}
.ed-tag--warn{background:rgba(224,163,65,.2); color:var(--spark);}
.ed-tag--bad{background:rgba(210,85,74,.2); color:var(--up);}
.ed-tag--neutral{background:var(--rule); color:var(--ink-2);}
.ed-progress{height:6px; background:var(--rule); border-radius:3px; overflow:hidden;}
.ed-progress > span{display:block; height:100%; background:var(--moss); transition:width .35s var(--ease);}
.ed-msg{margin-top:16px; padding:12px 14px; border-left:3px solid var(--moss); background:var(--card); font-size:.9rem; color:var(--ink-2);}

/* 카드 그리드 (점수 등) */
.ed-cardgrid{display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px;}
.ed-scard{border:1px solid var(--rule); background:var(--card); padding:15px;}
.ed-scard-head{display:flex; align-items:center; justify-content:space-between; gap:8px;}
.ed-scard-name{font-weight:800; font-size:.92rem;}
.ed-scard-score{font-family:var(--serif); font-size:.85rem; color:var(--ink-3); margin:7px 0 0;}
.ed-scard-text{font-size:.85rem; color:var(--ink-2); line-height:1.55; margin:7px 0 0;}
.ed-kv{display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:1px; background:var(--rule); border:1px solid var(--rule);}
.ed-kv-cell{background:var(--card); padding:11px 13px;}
.ed-kv-cell small{display:block; font-size:.72rem; color:var(--ink-3); font-weight:700;}
.ed-kv-cell span{font-size:.9rem; font-weight:700;}

.ed-fade{animation:ed-fade .4s var(--ease) both;}
@keyframes ed-fade{from{opacity:0; transform:translateY(10px);}to{opacity:1; transform:translateY(0);}}

@media (max-width:680px){
  .ed-nav-in{height:auto; padding-top:10px; padding-bottom:10px; flex-wrap:wrap;}
  .ed-nav-links{margin-left:0; width:100%; order:3;}
  .ed-quotegrid{grid-template-columns:repeat(2,1fr);}
}
@media (prefers-reduced-motion:reduce){
  .ed *{animation-duration:.001ms !important; transition-duration:.001ms !important;}
}
`;

/* ============================================================
   타입 · 상수 · 포맷 헬퍼 (기존 로직 그대로)
   ============================================================ */
type WorkspaceTab = "home" | "watchlist" | "analysis" | "history" | "assets";

const RECENT_STORAGE_KEY = "hqa.dashboard.recent";
const RECENT_LIMIT = 8;

const NAV_TABS: { id: WorkspaceTab; label: string }[] = [
  { id: "home", label: "홈" },
  { id: "watchlist", label: "워치리스트" },
  { id: "analysis", label: "AI 분석" },
  { id: "history", label: "거래 내역" },
  { id: "assets", label: "내 자산" }
];

function formatNumber(value: number | null | undefined) {
  if (value == null) return "-";
  return new Intl.NumberFormat("ko-KR").format(value);
}

function formatPrice(value: number | null | undefined) {
  if (value == null) return "-";
  return `${formatNumber(Math.round(value))}원`;
}

function formatSignedNumber(value: number | null | undefined) {
  if (value == null) return "-";
  const abs = formatNumber(Math.abs(Math.round(value)));
  if (value > 0) return `+${abs}`;
  if (value < 0) return `-${abs}`;
  return abs;
}

function formatSignedRate(value: number | null | undefined) {
  if (value == null) return "-";
  if (value > 0) return `+${value.toFixed(2)}%`;
  if (value < 0) return `${value.toFixed(2)}%`;
  return "0.00%";
}

function scoreTone(score: number | null | undefined): { bar: string } {
  if (score == null) return { bar: "var(--ink-3)" };
  if (score >= 70) return { bar: "var(--moss)" };
  if (score >= 50) return { bar: "var(--spark)" };
  return { bar: "var(--down)" };
}

function actionToneOf(action: string): { bg: string; fg: string } {
  const a = action.toLowerCase();
  if (a.includes("매수") || a.includes("buy")) {
    return { bg: "rgba(210,85,74,.2)", fg: "var(--up)" };
  }
  if (a.includes("매도") || a.includes("sell")) {
    return { bg: "rgba(93,131,214,.2)", fg: "var(--down)" };
  }
  return { bg: "var(--rule)", fg: "var(--ink-2)" };
}

function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const diff = Date.now() - t;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "방금 전";
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 전`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}일 전`;
  return new Date(iso).toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
}

function loadRecent(): StockSearchResult[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENT_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(0, RECENT_LIMIT) : [];
  } catch {
    return [];
  }
}

function saveRecent(items: StockSearchResult[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(items.slice(0, RECENT_LIMIT)));
  } catch {
    /* ignore quota errors */
  }
}

/* ============================================================
   대시보드
   ============================================================ */
export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [preference, setPreference] = useState<UserPreference | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([]);
  const [recent, setRecent] = useState<StockSearchResult[]>([]);
  const [selected, setSelected] = useState<StockSearchResult | null>(null);
  const [mode, setMode] = useState<AnalysisMode>("full");
  const [tab, setTab] = useState<WorkspaceTab>("home");
  const [balance, setBalance] = useState<Balance | null>(null);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balanceError, setBalanceError] = useState("");
  const [recentAnalyses, setRecentAnalyses] = useState<AnalysisHistoryItem[]>([]);
  const [recentAnalysesLoading, setRecentAnalysesLoading] = useState(false);
  const [indices, setIndices] = useState<MarketIndex[]>([]);
  const [ordersData, setOrdersData] = useState<Record<string, unknown> | null>(null);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [ordersError, setOrdersError] = useState("");
  const [message, setMessage] = useState("");
  const [loadingUser, setLoadingUser] = useState(true);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<AnalysisTaskResponse | null>(null);
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null);
  const [analysisProgress, setAnalysisProgress] = useState<AnalysisProgressEvent | null>(null);
  const [analysisError, setAnalysisError] = useState("");
  const analysisStreamRef = useRef<EventSource | null>(null);
  const [autoTradeEnabled, setAutoTradeEnabled] = useState(false);
  const [bulkAnalyzing, setBulkAnalyzing] = useState(false);

  useEffect(() => {
    setRecent(loadRecent());
  }, []);

  useEffect(() => {
    let active = true;

    authApi
      .me()
      .then(async (responseUser) => {
        if (!active) return;
        setUser(responseUser);

        if (!responseUser.surveyCompleted) {
          router.replace("/onboarding/preference");
          return;
        }

        try {
          const responsePreference = await authApi.getPreference();
          if (active) setPreference(responsePreference);
        } catch {
          if (active) setPreference(null);
        }
      })
      .catch(() => router.replace("/login"))
      .finally(() => {
        if (active) setLoadingUser(false);
      });

    tradingApi.status()
      .then((status) => {
        if (active) setAutoTradeEnabled(status.enabled);
      })
      .catch(() => { /* 무시: 자동매매 상태는 fail-safe로 OFF 유지 */ });

    return () => { active = false; };
  }, [router]);

  // 종목 클릭 → 상세 페이지로 이동.
  // selected는 AI 분석 탭에서 "직전에 본 종목을 분석" 흐름에 사용됨.
  function pickStock(stock: StockSearchResult) {
    if (selected?.code !== stock.code) {
      closeAnalysisStream();
      setAnalysisResult(null);
      setAnalysisProgress(null);
      setAnalysisError("");
      setTask(null);
    }
    setSelected(stock);
    setRecent((prev) => {
      const deduped = prev.filter((s) => s.code !== stock.code);
      const next = [stock, ...deduped].slice(0, RECENT_LIMIT);
      saveRecent(next);
      return next;
    });
    router.push(`/stocks/${stock.code}`);
  }

  async function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!searchQuery.trim()) return;

    setSearching(true);
    setMessage("");

    try {
      const response = await stockApi.search(searchQuery.trim());
      setSearchResults(response.results);
      if (response.results.length === 0) setMessage("검색 결과가 없습니다.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "종목 검색에 실패했습니다.");
    } finally {
      setSearching(false);
    }
  }

  const closeAnalysisStream = useCallback(() => {
    analysisStreamRef.current?.close();
    analysisStreamRef.current = null;
  }, []);

  const startAnalysisStream = useCallback((taskId: string) => {
    closeAnalysisStream();
    const source = new EventSource(eventStreamUrl(`/api/v1/analysis/${taskId}/stream`), {
      withCredentials: true
    });
    analysisStreamRef.current = source;

    source.addEventListener("progress", (event) => {
      try {
        setAnalysisProgress(JSON.parse((event as MessageEvent<string>).data) as AnalysisProgressEvent);
      } catch {
        /* ignore malformed progress payloads */
      }
    });

    source.addEventListener("completed", async () => {
      try {
        const latest = await analysisApi.result(taskId);
        setAnalysisResult(latest);
      } catch (e) {
        setAnalysisError(e instanceof Error ? e.message : "분석 결과를 불러오지 못했습니다.");
      } finally {
        closeAnalysisStream();
      }
    });

    source.onerror = async () => {
      try {
        const latest = await analysisApi.result(taskId);
        setAnalysisResult(latest);
        if (latest.status === "completed" || latest.status === "failed") {
          closeAnalysisStream();
        }
      } catch {
        /* keep the connection open for retries unless the result is final */
      }
    };
  }, [closeAnalysisStream]);

  useEffect(() => closeAnalysisStream, [closeAnalysisStream]);

  const loadOrders = useCallback(async () => {
    setOrdersLoading(true);
    setOrdersError("");
    try {
      const data = await tradingApi.orders({ limit: 20 });
      setOrdersData(data);
    } catch (e) {
      setOrdersError(e instanceof Error ? e.message : "주문 내역을 불러오지 못했습니다.");
    } finally {
      setOrdersLoading(false);
    }
  }, []);

  const loadBalance = useCallback(async () => {
    setBalanceLoading(true);
    setBalanceError("");
    try {
      const data = await tradingApi.balance();
      setBalance(data);
    } catch (e) {
      setBalance(null);
      setBalanceError(e instanceof Error ? e.message : "잔고를 불러오지 못했습니다.");
    } finally {
      setBalanceLoading(false);
    }
  }, []);

  const loadRecentAnalyses = useCallback(async () => {
    setRecentAnalysesLoading(true);
    try {
      const res = await analysisApi.history(1, 6);
      setRecentAnalyses(res.items ?? []);
    } catch {
      setRecentAnalyses([]);
    } finally {
      setRecentAnalysesLoading(false);
    }
  }, []);

  const loadIndices = useCallback(async () => {
    try {
      const res = await stockApi.indices();
      setIndices(res.items ?? []);
    } catch {
      setIndices([]);
    }
  }, []);

  useEffect(() => {
    if (tab === "history") {
      void loadOrders();
    }
    if (tab === "home") {
      void loadBalance();
      void loadOrders();
      void loadRecentAnalyses();
      void loadIndices();
    }
  }, [tab, loadOrders, loadBalance, loadRecentAnalyses, loadIndices]);

  async function submitAnalysis() {
    if (!selected) {
      setMessage("종목을 먼저 선택해주세요.");
      return;
    }

    setSubmitting(true);
    setMessage("");
    setAnalysisError("");
    setAnalysisResult(null);
    setAnalysisProgress(null);

    try {
      const response = await analysisApi.submit({
        stockName: selected.name,
        stockCode: selected.code,
        mode,
        maxRetries: mode === "full" ? 1 : 0
      });
      setTask(response);
      startAnalysisStream(response.taskId);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "분석 요청에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleBulkAnalyze() {
    if (bulkAnalyzing) return;
    const confirmed = window.confirm("워치리스트의 모든 종목을 분석할까요?");
    if (!confirmed) return;
    setBulkAnalyzing(true);
    setMessage("");
    try {
      const result = await analysisApi.bulk("quick", 0);
      if (result.submitted === 0) {
        setMessage("분석할 종목이 없습니다. (워치리스트 비어 있음)");
      } else {
        const failedNote = result.failed > 0 ? ` (실패 ${result.failed}건)` : "";
        setMessage(`${result.submitted}개 종목 분석을 시작했습니다${failedNote}. 분석 내역에서 확인하세요.`);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "전체 분석 요청에 실패했습니다.");
    } finally {
      setBulkAnalyzing(false);
    }
  }

  async function handleAutoTrade() {
    const next = !autoTradeEnabled;
    const confirmed = window.confirm(next ? "자동매매를 켤까요?" : "자동매매를 끌까요?");
    if (!confirmed) return;
    try {
      const status = await tradingApi.setAuto(next);
      setAutoTradeEnabled(status.enabled);
      setMessage(status.enabled ? "자동매매를 켰습니다." : "자동매매를 껐습니다.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "자동매매 토글에 실패했습니다.");
    }
  }

  async function logout() {
    await authApi.logout();
    router.push("/login");
  }

  const totalAssetsText = useMemo(() => {
    if (!preference?.totalAssets) return "-";
    return `${formatNumber(preference.totalAssets)}원`;
  }, [preference?.totalAssets]);

  const monthlyInvestmentText = useMemo(() => {
    if (!preference?.monthlyInvestment) return "-";
    return `${formatNumber(preference.monthlyInvestment)}원`;
  }, [preference?.monthlyInvestment]);

  if (loadingUser) {
    return (
      <div className="ed">
        <style dangerouslySetInnerHTML={{ __html: CSS }} />
        <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <p style={{ color: "var(--ink-3)", fontSize: "0.9rem" }}>불러오는 중...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="ed">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />

      {/* ── 네비 ── */}
      <nav className="ed-nav">
        <div className="ed-nav-in">
          <span className="ed-mark" aria-label="HQA">
            <b>HQA</b>
            <i />
          </span>
          <div className="ed-nav-links">
            {NAV_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`ed-nav-link${tab === t.id ? " ed-nav-link--on" : ""}`}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="ed-nav-right">
            <button
              type="button"
              className="ed-tlink"
              style={{ fontSize: ".84rem" }}
              onClick={() => router.push("/backtesting/ai")}
            >
              백테스트
            </button>
            <button
              type="button"
              className={`ed-statuschip${autoTradeEnabled ? " ed-statuschip--on" : ""}`}
              onClick={handleAutoTrade}
            >
              <span className={`ed-dot${autoTradeEnabled ? " ed-dot--live" : ""}`} />
              자동매매 {autoTradeEnabled ? "ON" : "OFF"}
            </button>
            <button type="button" className="ed-tlink" style={{ fontSize: ".84rem" }} onClick={logout}>
              로그아웃
            </button>
          </div>
        </div>
      </nav>

      <main className="ed-app">
       <div className="ed-wrap ed-fade" key={tab}>
        {tab === "home" && (
          <HomeTab
            user={user}
            preference={preference}
            balance={balance}
            balanceLoading={balanceLoading}
            balanceError={balanceError}
            ordersData={ordersData}
            ordersLoading={ordersLoading}
            autoTradeEnabled={autoTradeEnabled}
            recentAnalyses={recentAnalyses}
            recentAnalysesLoading={recentAnalysesLoading}
            indices={indices}
            onRefresh={() => { void loadBalance(); void loadOrders(); void loadRecentAnalyses(); void loadIndices(); }}
            onGoTab={setTab}
            onGoKis={() => router.push("/settings/kis")}
            onGoBacktest={() => router.push("/backtesting/ai")}
            onSelectStock={(code) => router.push(`/stocks/${code}`)}
          />
        )}

        {tab === "watchlist" && (
          <WatchlistTab
            user={user}
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            searching={searching}
            onSearch={onSearch}
            searchResults={searchResults}
            recent={recent}
            pickStock={pickStock}
          />
        )}

        {tab === "analysis" && (
          <AnalysisTab
            selected={selected}
            mode={mode}
            setMode={setMode}
            submitting={submitting}
            submitAnalysis={submitAnalysis}
            bulkAnalyzing={bulkAnalyzing}
            handleBulkAnalyze={handleBulkAnalyze}
            task={task}
            result={analysisResult}
            progress={analysisProgress}
            error={analysisError}
          />
        )}

        {tab === "history" && (
          <HistoryTab
            loading={ordersLoading}
            error={ordersError}
            data={ordersData}
            onRefresh={loadOrders}
          />
        )}

        {tab === "assets" && (
          <AssetsTab
            preference={preference}
            user={user}
            totalAssetsText={totalAssetsText}
            monthlyInvestmentText={monthlyInvestmentText}
            onGoKis={() => router.push("/settings/kis")}
            onGoPreference={() => router.push("/onboarding/preference")}
          />
        )}

        {message ? <p className="ed-msg">{message}</p> : null}
       </div>
      </main>
    </div>
  );
}

/* ============================================================
   워치리스트 탭 — 검색 · 관심 종목 목록 (클릭하면 상세 페이지)
   ============================================================ */
function WatchlistTab(props: {
  user: AuthUser | null;
  searchQuery: string;
  setSearchQuery: (v: string) => void;
  searching: boolean;
  onSearch: (e: FormEvent<HTMLFormElement>) => void;
  searchResults: StockSearchResult[];
  recent: StockSearchResult[];
  pickStock: (s: StockSearchResult) => void;
}) {
  const {
    user, searchQuery, setSearchQuery, searching, onSearch, searchResults, recent, pickStock
  } = props;

  return (
    <>
      <div className="ed-app-head">
        <div className="ed-kicker">워치리스트</div>
        <h1 className="ed-app-h">
          {user ? `${user.firstName}님의 관심 종목` : "관심 종목"}
        </h1>
      </div>

      <form className="ed-searchbar" onSubmit={onSearch}>
        <input
          className="ed-input"
          placeholder="종목명 · 코드 검색"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <button className="ed-btn ed-btn--ink" type="submit" disabled={searching}>
          {searching ? "검색 중..." : "검색"}
        </button>
      </form>

      {searchResults.length > 0 ? (
        <section className="ed-sec">
          <div className="ed-sec-head">
            <span className="ed-sec-title">검색 결과</span>
            <span className="ed-sec-meta">{searchResults.length}건</span>
          </div>
          <div className="ed-list">
            {searchResults.map((item) => (
              <button
                key={`s-${item.code}-${item.market}`}
                type="button"
                className="ed-row"
                onClick={() => pickStock(item)}
              >
                <span className="ed-row-mk">{item.name.slice(0, 1)}</span>
                <span className="ed-row-main">
                  <span className="ed-row-name">{item.name}</span>
                  <span className="ed-row-meta">{item.code} · {item.market}</span>
                </span>
                <span className="ed-row-num"><span className="ed-row-val" style={{ fontSize: ".95rem", color: "var(--ink-3)" }}>→</span></span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">최근 본 종목</span>
          <span className="ed-sec-meta">{recent.length}종목</span>
        </div>
        {recent.length === 0 ? (
          <p className="ed-hint" style={{ padding: "16px 4px" }}>
            위에서 종목을 검색하면 여기에 워치리스트가 쌓입니다.
          </p>
        ) : (
          <div className="ed-list">
            {recent.map((item) => (
              <button
                key={`r-${item.code}`}
                type="button"
                className="ed-row"
                onClick={() => pickStock(item)}
              >
                <span className="ed-row-mk">{item.name.slice(0, 1)}</span>
                <span className="ed-row-main">
                  <span className="ed-row-name">{item.name}</span>
                  <span className="ed-row-meta">{item.code} · {item.market}</span>
                </span>
                <span className="ed-row-num"><span className="ed-row-val" style={{ fontSize: ".95rem", color: "var(--ink-3)" }}>→</span></span>
              </button>
            ))}
          </div>
        )}
      </section>

      <p className="ed-hint" style={{ marginTop: 20 }}>
        종목을 누르면 차트 · 시세 · 주문, 뉴스/공시까지 모두 볼 수 있는 상세 페이지로 이동합니다.
      </p>
    </>
  );
}

/* ============================================================
   AI 분석 탭
   ============================================================ */
function AnalysisTab(props: {
  selected: StockSearchResult | null;
  mode: AnalysisMode;
  setMode: (m: AnalysisMode) => void;
  submitting: boolean;
  submitAnalysis: () => void;
  bulkAnalyzing: boolean;
  handleBulkAnalyze: () => void;
  task: AnalysisTaskResponse | null;
  result: AnalysisResult | null;
  progress: AnalysisProgressEvent | null;
  error: string;
}) {
  const { selected, mode, setMode, submitting, submitAnalysis, bulkAnalyzing, handleBulkAnalyze, task, result, progress, error } = props;
  return (
    <>
      <div className="ed-app-head">
        <div className="ed-kicker">AI 분석</div>
        <h1 className="ed-app-h">종목을 AI가 진단합니다</h1>
      </div>

      <p className="ed-hint">
        {selected
          ? `워치리스트에서 선택한 ${selected.name}을(를) 분석합니다.`
          : "워치리스트 탭에서 종목을 먼저 선택해주세요."}
      </p>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "center", marginTop: 18 }}>
        <div className="ed-seg">
          <button
            type="button"
            className={`ed-seg-btn${mode === "full" ? " ed-seg-btn--on" : ""}`}
            onClick={() => setMode("full")}
          >
            전체 분석
          </button>
          <button
            type="button"
            className={`ed-seg-btn${mode === "quick" ? " ed-seg-btn--on" : ""}`}
            onClick={() => setMode("quick")}
          >
            빠른 분석
          </button>
        </div>
        <button
          type="button"
          className="ed-btn ed-btn--moss"
          disabled={!selected || submitting}
          onClick={submitAnalysis}
        >
          {submitting ? "요청 중..." : "분석 시작"}
        </button>
        <button
          type="button"
          className="ed-btn ed-btn--line"
          disabled={bulkAnalyzing}
          onClick={handleBulkAnalyze}
        >
          {bulkAnalyzing ? "요청 중..." : "워치리스트 전체 분석"}
        </button>
      </div>

      {(task || result || progress || error) ? (
        <AnalysisPanel task={task} result={result} progress={progress} error={error} />
      ) : null}
    </>
  );
}

function translateAnalysisStatus(status?: string | null) {
  switch (status) {
    case "pending": return "대기 중";
    case "running": return "진행 중";
    case "completed": return "완료";
    case "failed": return "실패";
    default: return status ?? "-";
  }
}

function analysisStatusTone(status?: string | null): "good" | "warn" | "bad" {
  if (status === "completed") return "good";
  if (status === "failed") return "bad";
  return "warn";
}

function AnalysisPanel({
  task,
  result,
  progress,
  error
}: {
  task: AnalysisTaskResponse | null;
  result: AnalysisResult | null;
  progress: AnalysisProgressEvent | null;
  error: string;
}) {
  const status = result?.status ?? task?.status ?? "pending";
  const isFinished = status === "completed" || status === "failed";
  const percent = progress ? Math.round(progress.progress * 100) : null;

  return (
    <section className="ed-sec">
      <div className="ed-sec-head">
        <span className="ed-sec-title">분석 결과</span>
        <span className={`ed-tag ed-tag--${analysisStatusTone(status)}`}>
          {translateAnalysisStatus(status)}
        </span>
      </div>

      <p className="ed-hint" style={{ marginTop: 12 }}>
        {result?.mode === "quick" ? "빠른 분석" : result?.mode === "full" ? "전체 분석" : "진행 중"}
        {task?.taskId ? ` · ${task.taskId.slice(0, 8)}` : ""}
      </p>

      {!isFinished && progress ? (
        <div style={{ marginTop: 14, display: "grid", gap: 7 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: ".84rem" }}>
            <span style={{ color: "var(--ink-2)" }}>{progress.message}</span>
            {percent != null ? <span style={{ color: "var(--moss)", fontWeight: 800 }}>{percent}%</span> : null}
          </div>
          <div className="ed-progress">
            <span style={{ width: `${percent ?? 0}%` }} />
          </div>
        </div>
      ) : null}

      {error ? <p className="ed-msg" style={{ borderLeftColor: "var(--up)" }}>{error}</p> : null}

      {result?.scores?.length ? (
        <div className="ed-cardgrid" style={{ marginTop: 18 }}>
          {result.scores.map((score) => (
            <div className="ed-scard" key={score.agent}>
              <div className="ed-scard-head">
                <span className="ed-scard-name">{titleCaseAgent(score.agent)}</span>
                <span className="ed-tag ed-tag--neutral">{score.grade ?? "-"}</span>
              </div>
              <p className="ed-scard-score">{score.totalScore} / {score.maxScore}</p>
              {score.opinion ? <p className="ed-scard-text">{score.opinion}</p> : null}
            </div>
          ))}
        </div>
      ) : null}

      {result?.finalDecision && Object.keys(result.finalDecision).length > 0 ? (
        <div style={{ marginTop: 20 }}>
          <p className="ed-label" style={{ marginBottom: 8 }}>최종 판단</p>
          <div className="ed-kv">
            {Object.entries(result.finalDecision).map(([key, value]) => (
              <div className="ed-kv-cell" key={key}>
                <small>{key}</small>
                <span>{String(value)}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {result?.qualityWarnings?.length ? (
        <div style={{ marginTop: 20 }}>
          <p className="ed-label" style={{ marginBottom: 8, color: "var(--spark)" }}>경고</p>
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--spark)", fontSize: ".86rem", lineHeight: 1.7 }}>
            {result.qualityWarnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        </div>
      ) : null}

      {result?.errors && Object.keys(result.errors).length > 0 ? (
        <div style={{ marginTop: 20 }}>
          <p className="ed-label" style={{ marginBottom: 8, color: "var(--up)" }}>오류</p>
          {Object.entries(result.errors).map(([key, value]) => (
            <div key={key} className="ed-msg" style={{ borderLeftColor: "var(--up)", marginTop: 8 }}>
              <strong style={{ color: "var(--up)" }}>{key}</strong> · {value}
            </div>
          ))}
        </div>
      ) : null}

      {isFinished && !result?.scores?.length && !result?.errors && !error ? (
        <p className="ed-hint" style={{ marginTop: 14 }}>표시할 결과가 없습니다.</p>
      ) : null}
    </section>
  );
}

/* ============================================================
   거래 내역 탭
   ============================================================ */
function HistoryTab({
  loading,
  error,
  data,
  onRefresh
}: {
  loading: boolean;
  error: string;
  data: Record<string, unknown> | null;
  onRefresh: () => void;
}) {
  const orders = extractOrders(data);
  return (
    <>
      <div className="ed-app-head">
        <div className="ed-kicker">거래 내역</div>
        <h1 className="ed-app-h">최근 주문 기록</h1>
      </div>

      <div className="ed-sec-head">
        <span className="ed-sec-title">주문 내역</span>
        <button type="button" className="ed-btn ed-btn--line ed-btn--sm" onClick={onRefresh} disabled={loading}>
          {loading ? "불러오는 중..." : "새로고침"}
        </button>
      </div>

      {error ? <p className="ed-msg" style={{ borderLeftColor: "var(--up)" }}>{error}</p> : null}

      {!loading && orders.length === 0 && !error ? (
        <p className="ed-hint" style={{ padding: "20px 4px" }}>아직 주문 내역이 없어요.</p>
      ) : null}

      {orders.length > 0 ? (
        <div className="ed-list">
          {orders.map((o, i) => (
            <div className="ed-row ed-row--static" key={(o.id ?? `${o.code}-${i}`).toString()}>
              <span
                className="ed-row-mk"
                style={{
                  fontFamily: "var(--sans)",
                  fontSize: ".74rem",
                  fontWeight: 800,
                  color: o.side === "buy" ? "var(--up)" : "var(--down)"
                }}
              >
                {o.side === "buy" ? "매수" : o.side === "sell" ? "매도" : "—"}
              </span>
              <span className="ed-row-main">
                <span className="ed-row-name">{o.name ?? o.code ?? "-"}</span>
                <span className="ed-row-meta">
                  {o.code ?? ""}
                  {o.quantity != null ? ` · ${o.quantity}주` : ""}
                  {o.price != null ? ` · ${new Intl.NumberFormat("ko-KR").format(Number(o.price))}원` : ""}
                </span>
              </span>
              <span className="ed-row-num">
                <span className="ed-row-val" style={{ fontSize: ".9rem" }}>{o.status ?? "-"}</span>
                <span className="ed-row-pl" style={{ color: "var(--ink-3)", fontWeight: 700 }}>
                  {o.createdAt ?? ""}
                </span>
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}

function extractOrders(data: Record<string, unknown> | null): Array<{
  id?: string;
  code?: string;
  name?: string;
  side?: string;
  quantity?: number;
  price?: number;
  status?: string;
  createdAt?: string;
}> {
  if (!data) return [];
  const raw = (data.orders ?? data.items ?? data.results ?? []) as unknown;
  if (!Array.isArray(raw)) return [];
  return raw.map((row) => {
    const r = row as Record<string, unknown>;
    return {
      id: (r.id ?? r.orderId ?? r.order_id) as string | undefined,
      code: (r.code ?? r.stockCode ?? r.stock_code) as string | undefined,
      name: (r.name ?? r.stockName ?? r.stock_name) as string | undefined,
      side: (r.side ?? r.orderSide ?? r.order_side) as string | undefined,
      quantity: (r.quantity ?? r.qty) as number | undefined,
      price: (r.price ?? r.limitPrice ?? r.limit_price) as number | undefined,
      status: r.status as string | undefined,
      createdAt: (r.createdAt ?? r.created_at ?? r.timestamp) as string | undefined
    };
  });
}

/* ============================================================
   내 자산 탭
   ============================================================ */
function AssetsTab({
  preference,
  user,
  totalAssetsText,
  monthlyInvestmentText,
  onGoKis,
  onGoPreference
}: {
  preference: UserPreference | null;
  user: AuthUser | null;
  totalAssetsText: string;
  monthlyInvestmentText: string;
  onGoKis: () => void;
  onGoPreference: () => void;
}) {
  return (
    <>
      <div className="ed-app-head">
        <div className="ed-kicker">내 자산</div>
        <h1 className="ed-app-h">
          {user ? `${user.lastName}${user.firstName}님` : "내 자산"}
        </h1>
      </div>

      <div className="ed-figrow" style={{ marginTop: 8 }}>
        <div className="ed-fig ed-fig--xl">
          <small>보유 자산</small>
          <b>{totalAssetsText}</b>
        </div>
        <div className="ed-fig ed-fig--md">
          <small>월 투자 금액</small>
          <b>{monthlyInvestmentText}</b>
        </div>
      </div>

      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">투자 성향</span>
        </div>
        <div className="ed-kv">
          <div className="ed-kv-cell">
            <small>투자 기간</small>
            <span>{preference?.investmentPeriodMonths != null ? `${preference.investmentPeriodMonths}개월` : "-"}</span>
          </div>
          <div className="ed-kv-cell">
            <small>목표 수익률</small>
            <span>{preference?.targetReturnRate != null ? `${preference.targetReturnRate}%` : "-"}</span>
          </div>
          <div className="ed-kv-cell">
            <small>투자 성향</small>
            <span>{preference?.investmentType ?? "-"}</span>
          </div>
          <div className="ed-kv-cell">
            <small>변동성 허용</small>
            <span>{preference?.volatilityTolerance ?? "-"}</span>
          </div>
        </div>
        <p className="ed-fine" style={{ marginTop: 12 }}>
          더 정확한 평가 자산은 KIS 계좌를 연결한 뒤 확인할 수 있어요.
        </p>
      </section>

      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">계정 설정</span>
        </div>
        <div className="ed-list">
          <button type="button" className="ed-row" onClick={onGoKis}>
            <span className="ed-row-mk" style={{ fontFamily: "var(--sans)", fontSize: ".72rem", fontWeight: 800 }}>KIS</span>
            <span className="ed-row-main">
              <span className="ed-row-name">증권사 키 연결</span>
              <span className="ed-row-meta">한국투자증권 OpenAPI 키 관리</span>
            </span>
            <span className="ed-row-num"><span className="ed-row-val" style={{ fontSize: ".95rem", color: "var(--ink-3)" }}>→</span></span>
          </button>
          <button type="button" className="ed-row" onClick={onGoPreference}>
            <span className="ed-row-mk" style={{ fontFamily: "var(--sans)", fontSize: ".95rem" }}>◎</span>
            <span className="ed-row-main">
              <span className="ed-row-name">투자 성향 다시 설정</span>
              <span className="ed-row-meta">목표 · 위험 성향 · 투자 금액</span>
            </span>
            <span className="ed-row-num"><span className="ed-row-val" style={{ fontSize: ".95rem", color: "var(--ink-3)" }}>→</span></span>
          </button>
        </div>
      </section>
    </>
  );
}

/* ============================================================
   홈 탭 — 로그인 직후 보는 메인 대시보드
   자산 한 줄 · 보유 종목 Top · 최근 거래 · 빠른 이동
   ============================================================ */
function HomeTab(props: {
  user: AuthUser | null;
  preference: UserPreference | null;
  balance: Balance | null;
  balanceLoading: boolean;
  balanceError: string;
  ordersData: Record<string, unknown> | null;
  ordersLoading: boolean;
  autoTradeEnabled: boolean;
  recentAnalyses: AnalysisHistoryItem[];
  recentAnalysesLoading: boolean;
  indices: MarketIndex[];
  onRefresh: () => void;
  onGoTab: (t: WorkspaceTab) => void;
  onGoKis: () => void;
  onGoBacktest: () => void;
  onSelectStock: (code: string) => void;
}) {
  const {
    user, preference, balance, balanceLoading, balanceError,
    ordersData, autoTradeEnabled, recentAnalyses, recentAnalysesLoading, indices,
    onRefresh, onGoTab, onGoKis, onGoBacktest, onSelectStock
  } = props;

  const kisConfigured = !!user?.kisConfigured;
  const summary = balance?.summary;
  const holdings = balance?.holdings ?? [];
  const topHoldings = useMemo(
    () => [...holdings].sort((a, b) => b.evalAmount - a.evalAmount).slice(0, 5),
    [holdings]
  );
  const totalEval = summary?.totalEvalAmount ?? null;
  const totalProfit = summary?.totalEvalProfit ?? null;
  const totalPurchase = summary?.totalPurchaseAmount ?? null;
  const profitRate = (totalPurchase && totalPurchase > 0 && totalProfit != null)
    ? (totalProfit / totalPurchase) * 100
    : null;

  const orders = extractOrders(ordersData).slice(0, 5);
  const profitPositive = (totalProfit ?? 0) >= 0;

  const fallbackAssets = preference?.totalAssets ?? null;

  return (
    <>
      <div className="ed-app-head">
        <div className="ed-kicker">홈</div>
        <h1 className="ed-app-h">
          {user ? `${user.firstName}님, 환영합니다` : "환영합니다"}
        </h1>
        <p className="ed-greet" style={{ marginTop: 6 }}>
          {autoTradeEnabled
            ? "AI가 오늘도 자산을 돌보고 있어요."
            : "자동매매가 꺼져 있어요. 직접 운용 중입니다."}
        </p>

        {indices.length > 0 ? (
          <div
            style={{
              display: "flex",
              gap: 22,
              flexWrap: "wrap",
              marginTop: 14,
              paddingTop: 14,
              borderTop: "1px solid var(--rule)"
            }}
          >
            {indices.map((idx) => {
              const pos = idx.change >= 0;
              return (
                <div key={idx.code} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span style={{ fontSize: ".8rem", fontWeight: 800, color: "var(--ink-2)", letterSpacing: ".04em" }}>
                    {idx.name}
                  </span>
                  <span className="ed-tnum" style={{ fontFamily: "var(--serif)", fontSize: "1.1rem", fontWeight: 700 }}>
                    {idx.current.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </span>
                  <span
                    className={`ed-tnum ${pos ? "ed-up" : "ed-down"}`}
                    style={{ fontSize: ".86rem", fontWeight: 800 }}
                  >
                    {pos ? "+" : ""}{idx.change.toFixed(2)} ({pos ? "+" : ""}{idx.changeRate.toFixed(2)}%)
                  </span>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>

      {/* 자산 요약 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">내 자산</span>
          <button
            type="button"
            className="ed-btn ed-btn--line ed-btn--sm"
            onClick={onRefresh}
            disabled={balanceLoading}
          >
            {balanceLoading ? "불러오는 중..." : "새로고침"}
          </button>
        </div>

        {!kisConfigured ? (
          <div className="ed-msg" style={{ marginTop: 14 }}>
            증권 계좌를 연결하면 실제 평가금액과 보유 종목을 여기에 보여드려요.
            <div style={{ marginTop: 10 }}>
              <button type="button" className="ed-btn ed-btn--moss ed-btn--sm" onClick={onGoKis}>
                KIS 계좌 연결
              </button>
            </div>
          </div>
        ) : balanceError ? (
          <p className="ed-msg" style={{ borderLeftColor: "var(--up)", marginTop: 14 }}>
            {balanceError}
          </p>
        ) : (
          <div className="ed-figrow" style={{ marginTop: 14 }}>
            <div className="ed-fig ed-fig--xl">
              <small>총 평가자산</small>
              <b>
                {totalEval != null
                  ? formatPrice(totalEval)
                  : fallbackAssets != null
                    ? formatPrice(fallbackAssets)
                    : "-"}
              </b>
              {profitRate != null ? (
                <span className={`ed-fig-delta ${profitPositive ? "ed-up" : "ed-down"}`}>
                  {formatSignedNumber(totalProfit)}원 · {formatSignedRate(profitRate)}
                </span>
              ) : null}
            </div>
            <div className="ed-fig ed-fig--md">
              <small>예수금</small>
              <b>{summary?.deposit != null ? formatPrice(summary.deposit) : "-"}</b>
            </div>
            <div className="ed-fig ed-fig--md">
              <small>주식 평가</small>
              <b>{summary?.stockEvalAmount != null ? formatPrice(summary.stockEvalAmount) : "-"}</b>
            </div>
          </div>
        )}
      </section>

      {/* 보유 종목 */}
      {kisConfigured && holdings.length > 0 ? (
        <section className="ed-sec">
          <div className="ed-sec-head">
            <span className="ed-sec-title">보유 종목</span>
            <span className="ed-sec-meta">{holdings.length}종목 · 평가금액 순</span>
          </div>
          <div className="ed-list">
            {topHoldings.map((h) => {
              const pos = h.evalProfit >= 0;
              return (
                <button
                  type="button"
                  className="ed-row"
                  key={h.stockCode}
                  onClick={() => onSelectStock(h.stockCode)}
                >
                  <span className="ed-row-mk">{h.stockName.slice(0, 1)}</span>
                  <span className="ed-row-main">
                    <span className="ed-row-name">{h.stockName}</span>
                    <span className="ed-row-meta">
                      {h.stockCode} · {h.quantity}주 · 평단 {formatPrice(h.avgPrice)}
                    </span>
                  </span>
                  <span className="ed-row-num">
                    <span className="ed-row-val">{formatPrice(h.evalAmount)}</span>
                    <span className={`ed-row-pl ${pos ? "ed-up" : "ed-down"}`}>
                      {formatSignedNumber(h.evalProfit)}원 · {formatSignedRate(h.evalProfitRate)}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      {/* AI 활동 — 최근 분석 이력 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">AI 활동</span>
          <button
            type="button"
            className="ed-tlink"
            style={{ fontSize: ".82rem" }}
            onClick={() => onGoTab("analysis")}
          >
            전체 보기 →
          </button>
        </div>
        {recentAnalysesLoading && recentAnalyses.length === 0 ? (
          <p className="ed-hint" style={{ padding: "16px 4px" }}>AI 활동 불러오는 중...</p>
        ) : recentAnalyses.length === 0 ? (
          <p className="ed-hint" style={{ padding: "16px 4px" }}>
            아직 AI가 분석한 종목이 없어요. AI 분석 탭에서 첫 분석을 시작해보세요.
          </p>
        ) : (
          <>
            <p className="ed-hint" style={{ marginTop: 12, marginBottom: 10 }}>
              최근 {recentAnalyses.length}건의 분석 결과 — 점수가 높을수록 매수 신호가 강합니다.
            </p>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
                gap: 10
              }}
            >
              {recentAnalyses.map((a) => {
                const score = a.totalScore;
                const tone = scoreTone(score);
                const action = (a.action ?? "").trim();
                const actionTone = actionToneOf(action);
                return (
                  <button
                    key={a.taskId}
                    type="button"
                    className="ed-scard"
                    onClick={() => onSelectStock(a.stock.code)}
                    style={{
                      textAlign: "left",
                      cursor: "pointer",
                      borderLeft: `3px solid ${tone.bar}`,
                      background: "var(--card)"
                    }}
                  >
                    <div className="ed-scard-head">
                      <span className="ed-scard-name">{a.stock.name}</span>
                      {action ? (
                        <span
                          className="ed-tag"
                          style={{ background: actionTone.bg, color: actionTone.fg }}
                        >
                          {action}
                        </span>
                      ) : null}
                    </div>
                    <p className="ed-scard-score" style={{ color: tone.bar, fontWeight: 800 }}>
                      {score != null ? `${score.toFixed(1)}점` : "—"}
                      <span style={{ color: "var(--ink-3)", fontWeight: 600, marginLeft: 8 }}>
                        {a.stock.code} · {a.mode === "quick" ? "빠른" : "전체"}
                      </span>
                    </p>
                    <p className="ed-scard-text" style={{ color: "var(--ink-3)", fontSize: ".78rem" }}>
                      {formatTimeAgo(a.completedAt ?? a.createdAt)}
                    </p>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </section>

      {/* 최근 거래 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">최근 거래</span>
          <button
            type="button"
            className="ed-tlink"
            style={{ fontSize: ".82rem" }}
            onClick={() => onGoTab("history")}
          >
            전체 보기 →
          </button>
        </div>
        {orders.length === 0 ? (
          <p className="ed-hint" style={{ padding: "16px 4px" }}>
            아직 주문 내역이 없어요. 워치리스트에서 종목을 골라보세요.
          </p>
        ) : (
          <div className="ed-list">
            {orders.map((o, i) => (
              <div className="ed-row ed-row--static" key={(o.id ?? `${o.code}-${i}`).toString()}>
                <span
                  className="ed-row-mk"
                  style={{
                    fontFamily: "var(--sans)",
                    fontSize: ".74rem",
                    fontWeight: 800,
                    color: o.side === "buy" ? "var(--up)" : "var(--down)"
                  }}
                >
                  {o.side === "buy" ? "매수" : o.side === "sell" ? "매도" : "—"}
                </span>
                <span className="ed-row-main">
                  <span className="ed-row-name">{o.name ?? o.code ?? "-"}</span>
                  <span className="ed-row-meta">
                    {o.code ?? ""}
                    {o.quantity != null ? ` · ${o.quantity}주` : ""}
                    {o.price != null ? ` · ${formatPrice(Number(o.price))}` : ""}
                  </span>
                </span>
                <span className="ed-row-num">
                  <span className="ed-row-val" style={{ fontSize: ".9rem" }}>{o.status ?? "-"}</span>
                  <span className="ed-row-pl" style={{ color: "var(--ink-3)", fontWeight: 700 }}>
                    {o.createdAt ?? ""}
                  </span>
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 빠른 이동 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">바로가기</span>
        </div>
        <div className="ed-list">
          <button type="button" className="ed-row" onClick={() => onGoTab("watchlist")}>
            <span className="ed-row-mk" style={{ fontFamily: "var(--sans)", fontSize: ".74rem", fontWeight: 800 }}>WL</span>
            <span className="ed-row-main">
              <span className="ed-row-name">워치리스트</span>
              <span className="ed-row-meta">종목 검색 · 시세 · 직접 주문</span>
            </span>
            <span className="ed-row-num"><span className="ed-row-val" style={{ fontSize: ".95rem", color: "var(--ink-3)" }}>→</span></span>
          </button>
          <button type="button" className="ed-row" onClick={() => onGoTab("analysis")}>
            <span className="ed-row-mk" style={{ fontFamily: "var(--sans)", fontSize: ".74rem", fontWeight: 800 }}>AI</span>
            <span className="ed-row-main">
              <span className="ed-row-name">AI 분석</span>
              <span className="ed-row-meta">종목 진단 · 전체 워치리스트 분석</span>
            </span>
            <span className="ed-row-num"><span className="ed-row-val" style={{ fontSize: ".95rem", color: "var(--ink-3)" }}>→</span></span>
          </button>
          <button type="button" className="ed-row" onClick={() => onGoTab("assets")}>
            <span className="ed-row-mk" style={{ fontFamily: "var(--sans)", fontSize: ".95rem" }}>◎</span>
            <span className="ed-row-main">
              <span className="ed-row-name">내 자산 · 투자 성향</span>
              <span className="ed-row-meta">목표 · 위험 성향 관리</span>
            </span>
            <span className="ed-row-num"><span className="ed-row-val" style={{ fontSize: ".95rem", color: "var(--ink-3)" }}>→</span></span>
          </button>
          <button type="button" className="ed-row" onClick={onGoBacktest}>
            <span className="ed-row-mk" style={{ fontFamily: "var(--sans)", fontSize: ".72rem", fontWeight: 800 }}>BT</span>
            <span className="ed-row-main">
              <span className="ed-row-name">AI 백테스트 결과</span>
              <span className="ed-row-meta">전략 비교 보고서</span>
            </span>
            <span className="ed-row-num"><span className="ed-row-val" style={{ fontSize: ".95rem", color: "var(--ink-3)" }}>→</span></span>
          </button>
        </div>
      </section>
    </>
  );
}
