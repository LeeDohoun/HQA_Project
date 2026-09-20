"use client";

/* ============================================================
   대시보드 — 워치리스트 / AI 분석 / 거래 내역 / 내 자산 4탭
   ============================================================ */

import { useRouter } from "next/navigation";
import { Dispatch, FormEvent, SetStateAction, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentDetailSections, AnalysisSummaryCard } from "@/components/common/analysis-report";
import { analysisApi, authApi, stockApi, tradingApi, watchlistApi } from "@/lib/api";
import type {
  AnalysisHistoryItem,
  AnalysisProgressEvent,
  AnalysisResult,
  AnalysisTaskResponse,
  AiActivityResponse,
  AutoTradeExplanation,
  AuthUser,
  Balance,
  MarketIndex,
  StockSearchResult,
  UserPreference
} from "@/types/api";

/* ============================================================
   디자인 시스템 (에디토리얼 · 다크)
   ============================================================ */

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
  const [watchlist, setWatchlist] = useState<StockSearchResult[]>([]);
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [selectedAnalysisCodes, setSelectedAnalysisCodes] = useState<string[]>([]);
  const [selected, setSelected] = useState<StockSearchResult | null>(null);
  const [tab, setTab] = useState<WorkspaceTab>("home");
  const [balance, setBalance] = useState<Balance | null>(null);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balanceError, setBalanceError] = useState("");
  const [recentAnalyses, setRecentAnalyses] = useState<AnalysisHistoryItem[]>([]);
  const [recentAnalysesLoading, setRecentAnalysesLoading] = useState(false);
  const [aiActivity, setAiActivity] = useState<AiActivityResponse | null>(null);
  const [aiActivityLoading, setAiActivityLoading] = useState(false);
  const [autoTradeExplanations, setAutoTradeExplanations] = useState<AutoTradeExplanation[]>([]);
  const [autoTradeExplanationsLoading, setAutoTradeExplanationsLoading] = useState(false);
  const [indices, setIndices] = useState<MarketIndex[]>([]);
  const [ordersData, setOrdersData] = useState<Record<string, unknown> | null>(null);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [ordersError, setOrdersError] = useState("");
  const [message, setMessage] = useState("");
  const [loadingUser, setLoadingUser] = useState(true);
  const [searching, setSearching] = useState(false);
  const [task, setTask] = useState<AnalysisTaskResponse | null>(null);
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null);
  const [analysisProgress, setAnalysisProgress] = useState<AnalysisProgressEvent | null>(null);
  const [analysisError, setAnalysisError] = useState("");
  const [bulkTasks, setBulkTasks] = useState<AnalysisTaskResponse[]>([]);
  const analysisProgressPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activeAnalysisTaskRef = useRef<{ taskId: string } | null>(null);
  const [autoTradeEnabled, setAutoTradeEnabled] = useState(false);
  const [autoTradeConfirmOpen, setAutoTradeConfirmOpen] = useState(false);
  const [autoTradeSaving, setAutoTradeSaving] = useState(false);
  const [bulkAnalyzing, setBulkAnalyzing] = useState(false);
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);

  useEffect(() => {
    setRecent(loadRecent());
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

  const loadWatchlist = useCallback(async () => {
    setWatchlistLoading(true);
    try {
      const response = await watchlistApi.list();
      const items = response.items.map((item) => ({
        name: item.name,
        code: item.code,
        market: item.market
      }));
      setWatchlist(items);
      setSelectedAnalysisCodes((prev) => prev.filter((code) => items.some((item) => item.code === code)));
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "워치리스트를 불러오지 못했습니다.");
      setWatchlist([]);
      setSelectedAnalysisCodes([]);
    } finally {
      setWatchlistLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;

    authApi
      .me()
      .then(async (responseUser) => {
        if (!active) return;
        setUser(responseUser);
        void loadWatchlist();

        if (!responseUser.surveyCompleted) {
          router.replace("/onboarding/preference");
          return;
        }

        // KIS 계좌가 연결된 사용자는 로그인 직후 곧바로 모의계좌 잔고를 불러와
        // 앱 전역(상단 네비)에 계정 전체 잔고로 표시한다.
        if (responseUser.kisConfigured) {
          void loadBalance();
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
  }, [router, loadBalance, loadWatchlist]);

  // 종목 클릭 → 상세 페이지로 이동.
  function pickStock(stock: StockSearchResult) {
    closeAnalysisProgressPoll();
    if (selected?.code !== stock.code) {
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

  async function addToWatchlist(stock: StockSearchResult) {
    setMessage("");
    try {
      const saved = await watchlistApi.add(stock);
      setWatchlist((prev) => {
        const nextStock = { name: saved.name, code: saved.code, market: saved.market };
        const withoutDuplicate = prev.filter((item) => item.code !== saved.code);
        return [nextStock, ...withoutDuplicate];
      });
      setMessage(`${saved.name}을(를) 워치리스트에 추가했습니다.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "워치리스트 추가에 실패했습니다.");
    }
  }

  async function removeFromWatchlist(stockCode: string) {
    setMessage("");
    try {
      await watchlistApi.remove(stockCode);
      setWatchlist((prev) => prev.filter((item) => item.code !== stockCode));
      setSelectedAnalysisCodes((prev) => prev.filter((code) => code !== stockCode));
      setMessage("워치리스트에서 삭제했습니다.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "워치리스트 삭제에 실패했습니다.");
    }
  }

  const closeAnalysisProgressPoll = useCallback(() => {
    activeAnalysisTaskRef.current = null;
    if (analysisProgressPollRef.current) {
      clearTimeout(analysisProgressPollRef.current);
      analysisProgressPollRef.current = null;
    }
  }, []);

  const startAnalysisPolling = useCallback((taskId: string) => {
    closeAnalysisProgressPoll();
    const tracking = { taskId };
    activeAnalysisTaskRef.current = tracking;
    async function poll() {
      try {
        const result = await analysisApi.result(taskId);
        if (activeAnalysisTaskRef.current !== tracking) return;
        setAnalysisError("");
        setAnalysisResult(result);
        setTask((current) => current?.taskId === taskId ? { ...current, status: result.status } : current);
        setBulkTasks((current) => current.map((item) => item.taskId === taskId ? { ...item, status: result.status } : item));
        const complete = result.status === "completed" || result.status === "failed";
        setAnalysisProgress({ agent: "analysis", status: result.status,
          message: complete ? "공통 종목 분석이 종료되었습니다" : "공통 종목 분석을 진행 중입니다",
          progress: complete ? 1 : 0, timestamp: result.completedAt ?? result.createdAt });
        if (complete) {
          closeAnalysisProgressPoll();
          return;
        }
        analysisProgressPollRef.current = setTimeout(poll, 5000);
      } catch (error) {
        if (activeAnalysisTaskRef.current !== tracking) return;
        setAnalysisError(error instanceof Error ? error.message : "분석 결과를 불러오지 못했습니다.");
        closeAnalysisProgressPoll();
      }
    }
    void poll();
  }, [closeAnalysisProgressPoll]);

  useEffect(() => () => closeAnalysisProgressPoll(), [closeAnalysisProgressPoll]);

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

  const loadAiActivity = useCallback(async () => {
    setAiActivityLoading(true);
    try {
      const res = await tradingApi.aiActivity(6);
      setAiActivity(res);
    } catch {
      setAiActivity(null);
    } finally {
      setAiActivityLoading(false);
    }
  }, []);

  const loadAutoTradeExplanations = useCallback(async () => {
    setAutoTradeExplanationsLoading(true);
    try {
      const res = await tradingApi.explanations(6);
      setAutoTradeExplanations(res.items ?? []);
    } catch {
      setAutoTradeExplanations([]);
    } finally {
      setAutoTradeExplanationsLoading(false);
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
      void loadAutoTradeExplanations();
    }
    if (tab === "home") {
      void loadBalance();
      void loadOrders();
      void loadRecentAnalyses();
      void loadAiActivity();
      void loadAutoTradeExplanations();
      void loadIndices();
    }
  }, [tab, loadOrders, loadBalance, loadRecentAnalyses, loadAiActivity, loadAutoTradeExplanations, loadIndices]);

  function requestBulkAnalyze() {
    if (bulkAnalyzing) return;
    const selectedStocks = watchlist.filter((stock) => selectedAnalysisCodes.includes(stock.code));
    if (selectedStocks.length === 0) {
      setMessage("AI 분석할 워치리스트 종목을 선택해주세요.");
      return;
    }
    setMessage("");
    setBulkConfirmOpen(true);
  }

  async function handleBulkAnalyze() {
    if (bulkAnalyzing) return;
    const selectedStocks = watchlist.filter((stock) => selectedAnalysisCodes.includes(stock.code));
    if (selectedStocks.length === 0) {
      setMessage("AI 분석할 워치리스트 종목을 선택해주세요.");
      setBulkConfirmOpen(false);
      return;
    }
    setBulkConfirmOpen(false);
    setBulkAnalyzing(true);
    setMessage("");
    try {
      const result = await analysisApi.bulk(
        "full",
        0,
        selectedStocks.map((stock) => ({ stockName: stock.name, stockCode: stock.code }))
      );
      if (result.submitted === 0) {
        setMessage(result.failures.map((failure) => `${failure.stockName}: ${failure.reason}`).join(" · ") || "분석할 종목이 없습니다.");
        setBulkTasks([]);
      } else {
        const failedNote = result.failed > 0 ? ` (실패 ${result.failed}건)` : "";
        const firstTask = result.tasks[0] ?? null;
        setTask(firstTask);
        setAnalysisResult(null);
        setAnalysisProgress(null);
        setAnalysisError("");
        setBulkTasks(result.tasks);
        if (firstTask) {
          startAnalysisPolling(firstTask.taskId);
        }
        setMessage(`${result.submitted}개 종목 분석을 시작했습니다${failedNote}. 첫 번째 종목의 작업 상태를 표시합니다.`);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "분석 요청에 실패했습니다.");
    } finally {
      setBulkAnalyzing(false);
    }
  }

  function trackAnalysisTask(nextTask: AnalysisTaskResponse) {
    setTask(nextTask);
    setAnalysisResult(null);
    setAnalysisProgress(null);
    setAnalysisError("");
    startAnalysisPolling(nextTask.taskId);
  }

  async function handleAutoTrade() {
    setAutoTradeConfirmOpen(true);
  }

  async function confirmAutoTradeToggle() {
    const next = !autoTradeEnabled;
    setAutoTradeSaving(true);
    try {
      const status = await tradingApi.setAuto(next);
      setAutoTradeEnabled(status.enabled);
      setAutoTradeConfirmOpen(false);
      setMessage(status.enabled ? "자동매매를 켰습니다." : "자동매매를 껐습니다.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "자동매매 토글에 실패했습니다.");
    } finally {
      setAutoTradeSaving(false);
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
      <div className="workspace">
        <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <p style={{ color: "var(--ink-3)", fontSize: "0.9rem" }}>불러오는 중...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="workspace">

      {/* ── 네비 ── */}
      <nav className="ed-nav" aria-label="워크스페이스 메뉴">
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
                aria-current={tab === t.id ? "page" : undefined}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="ed-nav-right">
            {user?.kisConfigured ? (
              <button
                type="button"
                className={`ed-navbal${balanceLoading ? " ed-navbal--loading" : ""}`}
                onClick={() => setTab("home")}
                title="KIS 계좌 전체 잔고"
              >
                <small>계정 전체 잔고</small>
                <b>
                  {balanceLoading
                    ? "불러오는 중..."
                    : balance?.summary?.totalEvalAmount != null
                      ? formatPrice(balance.summary.totalEvalAmount)
                      : "-"}
                </b>
              </button>
            ) : null}
            <button
              type="button"
              className={`ed-statuschip${autoTradeEnabled ? " ed-statuschip--on" : ""}`}
              onClick={handleAutoTrade}
              disabled={autoTradeSaving}
            >
              <span className={`ed-dot${autoTradeEnabled ? " ed-dot--live" : ""}`} />
              {autoTradeSaving ? "변경 중..." : `모의 자동매매 ${autoTradeEnabled ? "ON" : "OFF"}`}
            </button>
            <button type="button" className="ed-tlink" style={{ fontSize: ".84rem" }} onClick={logout}>
              로그아웃
            </button>
          </div>
        </div>
      </nav>

      {autoTradeConfirmOpen ? (
        <div className="ed-modal-backdrop" role="presentation" onMouseDown={() => !autoTradeSaving && setAutoTradeConfirmOpen(false)}>
          <section
            className="ed-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="auto-trade-confirm-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="ed-modal-kicker">
              <span className={`ed-dot${autoTradeEnabled ? " ed-dot--live" : ""}`} />
              AUTO TRADING
            </div>
            <h2 id="auto-trade-confirm-title" className="ed-modal-title">
              자동매매를 {autoTradeEnabled ? "중지할까요?" : "시작할까요?"}
            </h2>
            <p className="ed-modal-copy">
              {autoTradeEnabled
                ? "OFF로 전환하면 AI 자동매매 루프를 중지하고, 이후 대기 신호도 집행하지 않습니다."
                : "ON으로 전환하면 모의투자 자동매매 루프가 시작되고, 백엔드 스케줄러도 이 계정을 자동매매 대상으로 처리합니다."}
            </p>
            <ul className="ed-modal-list">
              <li>모의투자 KIS 계정 기준으로 주문 흐름을 실행합니다.</li>
              <li>생성된 매매 판단과 거절 사유는 거래 내역의 AI 매매근거에서 확인할 수 있습니다.</li>
            </ul>
            <div className="ed-modal-actions">
              <button
                type="button"
                className="ed-btn ed-btn--line"
                onClick={() => setAutoTradeConfirmOpen(false)}
                disabled={autoTradeSaving}
              >
                취소
              </button>
              <button
                type="button"
                className={autoTradeEnabled ? "ed-btn ed-btn--ink" : "ed-btn ed-btn--moss"}
                onClick={confirmAutoTradeToggle}
                disabled={autoTradeSaving}
              >
                {autoTradeSaving ? "처리 중..." : autoTradeEnabled ? "자동매매 끄기" : "자동매매 켜기"}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      <main className="ed-app">
       <div className="ed-wrap ed-fade" key={tab}>
        {tab === "home" && (
          <HomeTab
            user={user}
            balance={balance}
            balanceLoading={balanceLoading}
            balanceError={balanceError}
            ordersData={ordersData}
            ordersLoading={ordersLoading}
            autoTradeEnabled={autoTradeEnabled}
            recentAnalyses={recentAnalyses}
            recentAnalysesLoading={recentAnalysesLoading}
            aiActivity={aiActivity}
            aiActivityLoading={aiActivityLoading}
            autoTradeExplanations={autoTradeExplanations}
            autoTradeExplanationsLoading={autoTradeExplanationsLoading}
            indices={indices}
            onRefresh={() => { void loadBalance(); void loadOrders(); void loadRecentAnalyses(); void loadAiActivity(); void loadAutoTradeExplanations(); void loadIndices(); }}
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
            watchlist={watchlist}
            watchlistLoading={watchlistLoading}
            recent={recent}
            pickStock={pickStock}
            addToWatchlist={addToWatchlist}
            removeFromWatchlist={removeFromWatchlist}
          />
        )}

        {tab === "analysis" && (
          <AnalysisTab
            watchlist={watchlist}
            watchlistLoading={watchlistLoading}
            selectedAnalysisCodes={selectedAnalysisCodes}
            setSelectedAnalysisCodes={setSelectedAnalysisCodes}
            bulkAnalyzing={bulkAnalyzing}
            bulkConfirmOpen={bulkConfirmOpen}
            requestBulkAnalyze={requestBulkAnalyze}
            confirmBulkAnalyze={handleBulkAnalyze}
            cancelBulkAnalyze={() => setBulkConfirmOpen(false)}
            autoTradeEnabled={autoTradeEnabled}
            task={task}
            bulkTasks={bulkTasks}
            onTrackTask={trackAnalysisTask}
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
            explanations={autoTradeExplanations}
            explanationsLoading={autoTradeExplanationsLoading}
            onRefresh={() => { void loadOrders(); void loadAutoTradeExplanations(); }}
            onSelectStock={(code) => router.push(`/stocks/${code}`)}
          />
        )}

        {tab === "assets" && (
          <AssetsTab
            preference={preference}
            user={user}
            balance={balance}
            balanceLoading={balanceLoading}
            balanceError={balanceError}
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
  watchlist: StockSearchResult[];
  watchlistLoading: boolean;
  recent: StockSearchResult[];
  pickStock: (s: StockSearchResult) => void;
  addToWatchlist: (s: StockSearchResult) => void;
  removeFromWatchlist: (stockCode: string) => void;
}) {
  const {
    user, searchQuery, setSearchQuery, searching, onSearch, searchResults,
    watchlist, watchlistLoading, recent, pickStock, addToWatchlist, removeFromWatchlist
  } = props;
  const watchlistCodes = new Set(watchlist.map((item) => item.code));

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
              <div
                key={`s-${item.code}-${item.market}`}
                className="ed-row ed-row--static"
              >
                <span className="ed-row-mk">{item.name.slice(0, 1)}</span>
                <span className="ed-row-main">
                  <span className="ed-row-name">{item.name}</span>
                  <span className="ed-row-meta">{item.code} · {item.market}</span>
                </span>
                <span className="ed-row-num" style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button type="button" className="ed-btn ed-btn--line ed-btn--sm" onClick={() => pickStock(item)}>
                    보기
                  </button>
                  <button
                    type="button"
                    className="ed-btn ed-btn--moss ed-btn--sm"
                    disabled={watchlistCodes.has(item.code)}
                    onClick={() => addToWatchlist(item)}
                  >
                    {watchlistCodes.has(item.code) ? "등록됨" : "추가"}
                  </button>
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">등록한 관심 종목</span>
          <span className="ed-sec-meta">{watchlistLoading ? "불러오는 중" : `${watchlist.length}종목`}</span>
        </div>
        {watchlist.length === 0 ? (
          <p className="ed-hint" style={{ padding: "16px 4px" }}>
            종목을 검색한 뒤 추가 버튼으로 워치리스트에 등록하세요.
          </p>
        ) : (
          <div className="ed-list">
            {watchlist.map((item) => (
              <div
                key={`w-${item.code}`}
                className="ed-row ed-row--static"
              >
                <span className="ed-row-mk">{item.name.slice(0, 1)}</span>
                <span className="ed-row-main">
                  <span className="ed-row-name">{item.name}</span>
                  <span className="ed-row-meta">{item.code} · {item.market}</span>
                </span>
                <span className="ed-row-num" style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button type="button" className="ed-btn ed-btn--line ed-btn--sm" onClick={() => pickStock(item)}>
                    보기
                  </button>
                  <button type="button" className="ed-btn ed-btn--line ed-btn--sm" onClick={() => removeFromWatchlist(item.code)}>
                    삭제
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">최근 본 종목</span>
          <span className="ed-sec-meta">{recent.length}종목</span>
        </div>
        {recent.length === 0 ? (
          <p className="ed-hint" style={{ padding: "16px 4px" }}>
            종목 상세 화면을 열면 최근 본 종목이 여기에 표시됩니다.
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
  watchlist: StockSearchResult[];
  watchlistLoading: boolean;
  selectedAnalysisCodes: string[];
  setSelectedAnalysisCodes: Dispatch<SetStateAction<string[]>>;
  bulkAnalyzing: boolean;
  bulkConfirmOpen: boolean;
  requestBulkAnalyze: () => void;
  confirmBulkAnalyze: () => void;
  cancelBulkAnalyze: () => void;
  autoTradeEnabled: boolean;
  task: AnalysisTaskResponse | null;
  bulkTasks: AnalysisTaskResponse[];
  onTrackTask: (task: AnalysisTaskResponse) => void;
  result: AnalysisResult | null;
  progress: AnalysisProgressEvent | null;
  error: string;
}) {
  const {
    watchlist, watchlistLoading, selectedAnalysisCodes, setSelectedAnalysisCodes,
    bulkAnalyzing, bulkConfirmOpen,
    requestBulkAnalyze, confirmBulkAnalyze, cancelBulkAnalyze,
    autoTradeEnabled, task, bulkTasks, onTrackTask, result, progress, error
  } = props;
  const selectedSet = new Set(selectedAnalysisCodes);
  const selectedCount = watchlist.filter((stock) => selectedSet.has(stock.code)).length;
  const toggleAnalysisStock = (code: string) => {
    setSelectedAnalysisCodes((prev) =>
      prev.includes(code) ? prev.filter((item) => item !== code) : [...prev, code]
    );
  };
  const currentTaskLabel = task?.message.replace(/\s*analysis queued\s*$/i, "");

  return (
    <>
      <div className="ed-app-head">
        <div className="ed-kicker">AI 분석</div>
        <h1 className="ed-app-h">분석 작업 콘솔</h1>
      </div>

      <div className="ed-console-hero">
        <div className="ed-console-card ed-console-card--primary">
          <div className="ed-eyebrow">
            <span className={`ed-dot${task && !result ? " ed-dot--live" : ""}`} />
            {task ? "작업 추적 중" : "새 분석 준비"}
          </div>
          <h2 className="ed-console-title" style={{ marginTop: 10 }}>
            {currentTaskLabel ?? "워치리스트에서 분석 대상을 선택하세요"}
          </h2>
          <p className="ed-console-sub">
            {task
              ? `${task.taskId.slice(0, 8)} 작업의 상태와 완료된 분석 결과를 표시합니다.`
              : selectedCount > 0
                ? `${selectedCount}개 종목이 선택됐습니다. 공통 종목 분석으로 실행할 수 있습니다.`
                : "워치리스트에서 분석할 종목을 체크한 뒤 선택 종목 실행을 누르세요."}
          </p>

          <div className="ed-console-actions">
            <button
              type="button"
              className="ed-btn ed-btn--moss"
              disabled={bulkAnalyzing || selectedCount === 0}
              onClick={requestBulkAnalyze}
            >
              {bulkAnalyzing ? "요청 중..." : `선택 종목 실행${selectedCount ? ` (${selectedCount})` : ""}`}
            </button>
          </div>

          {bulkConfirmOpen ? (
            <div className="ed-confirm-panel">
              <div>
                <div className="ed-confirm-title">
                  {selectedCount}개 종목을 공통 종목 분석으로 실행합니다
                </div>
                <p className="ed-confirm-copy">
                  Analyst, Quant, Chartist가 공시·재무·가격을 분석합니다. 완료 시간은 데이터와 요청 대기 상황에 따라 달라집니다.
                </p>
              </div>
              <div className="ed-confirm-actions">
                <button
                  type="button"
                  className="ed-btn ed-btn--moss ed-btn--sm"
                  onClick={confirmBulkAnalyze}
                  disabled={bulkAnalyzing}
                >
                  실행
                </button>
                <button
                  type="button"
                  className="ed-btn ed-btn--line ed-btn--sm"
                  onClick={cancelBulkAnalyze}
                  disabled={bulkAnalyzing}
                >
                  취소
                </button>
              </div>
            </div>
          ) : null}

          <div className="ed-pillbar">
            <span className="ed-pill ed-pill--live">공통 종목 분석</span>
            <span className="ed-pill">선택 {selectedCount}종목</span>
            {autoTradeEnabled ? <span className="ed-pill ed-pill--live">자동매매 ON</span> : null}
          </div>
        </div>

        <div className="ed-console-card">
          <div className="ed-console-title">실행 요약</div>
          <div className="ed-console-stats">
            <div className="ed-console-stat">
              <small>WATCHLIST</small>
              <b>{watchlistLoading ? "-" : watchlist.length}</b>
            </div>
            <div className="ed-console-stat">
              <small>SELECTED</small>
              <b>{selectedCount}</b>
            </div>
            <div className="ed-console-stat">
              <small>QUEUE</small>
              <b>{bulkTasks.length}</b>
            </div>
          </div>
          <p className="ed-console-sub" style={{ marginTop: 12 }}>
            선택 종목의 세 전문가 결과를 조회합니다. 이 화면의 분석 실행으로 주문이 제출되지는 않습니다.
          </p>
        </div>
      </div>

      {autoTradeEnabled ? (
        <p className="ed-msg" style={{ marginTop: 14 }}>
          자동매매 분석과 공통 종목 분석은 동일한 모델 호출 한도를 사용합니다.
        </p>
      ) : null}

      <div className="ed-console-grid">
        <div className="ed-console-col">
          <section className="ed-console-panel">
            <div className="ed-panel-head">
              <div>
                <div className="ed-panel-title">분석 대상</div>
                <div className="ed-panel-meta">
                  {watchlistLoading ? "불러오는 중" : `${selectedCount}/${watchlist.length} 선택`}
                </div>
              </div>
              <span className="ed-tag ed-tag--neutral">WATCHLIST</span>
            </div>

            {watchlist.length === 0 ? (
              <div className="ed-empty-panel" style={{ marginTop: 12 }}>
                워치리스트 탭에서 관심 종목을 먼저 등록하세요.
              </div>
            ) : (
              <>
                <div className="ed-watch-tools">
                  <button
                    type="button"
                    className="ed-btn ed-btn--line ed-btn--sm"
                    onClick={() => setSelectedAnalysisCodes(watchlist.map((stock) => stock.code))}
                  >
                    전체 선택
                  </button>
                  <button
                    type="button"
                    className="ed-btn ed-btn--line ed-btn--sm"
                    onClick={() => setSelectedAnalysisCodes([])}
                  >
                    선택 해제
                  </button>
                </div>
                <div className="ed-stock-grid">
                  {watchlist.map((stock) => {
                    const checked = selectedSet.has(stock.code);
                    return (
                      <button
                        type="button"
                        className={`ed-stock-pick${checked ? " ed-stock-pick--on" : ""}`}
                        key={`analysis-${stock.code}`}
                        onClick={() => toggleAnalysisStock(stock.code)}
                      >
                        <span className="ed-check">{checked ? "✓" : ""}</span>
                        <span className="ed-row-main">
                          <span className="ed-row-name">{stock.name}</span>
                          <span className="ed-row-meta">{stock.code} · {stock.market}</span>
                        </span>
                        <span className={`ed-tag ed-tag--${checked ? "good" : "neutral"}`}>
                          {checked ? "선택" : "대기"}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </section>

          {bulkTasks.length > 0 ? (
            <BulkAnalysisPanel tasks={bulkTasks} activeTaskId={task?.taskId ?? null} onTrackTask={onTrackTask} />
          ) : null}
        </div>

        <div className="ed-console-col">
          {(task || result || progress || error) ? (
            <AnalysisPanel task={task} result={result} progress={progress} error={error} />
          ) : (
            <section className="ed-console-panel">
              <div className="ed-panel-head">
                <div>
                  <div className="ed-panel-title">라이브 분석</div>
                  <div className="ed-panel-meta">대기 중</div>
                </div>
                <span className="ed-tag ed-tag--neutral">IDLE</span>
              </div>
              <div className="ed-empty-panel" style={{ marginTop: 14 }}>
                분석을 실행하면 이 영역에 작업 상태와 세 전문가의 분석 결과가 표시됩니다.
              </div>
            </section>
          )}
        </div>
      </div>
    </>
  );
}

function BulkAnalysisPanel({
  tasks,
  activeTaskId,
  onTrackTask
}: {
  tasks: AnalysisTaskResponse[];
  activeTaskId: string | null;
  onTrackTask: (task: AnalysisTaskResponse) => void;
}) {
  return (
    <section className="ed-console-panel" style={{ marginTop: 14 }}>
      <div className="ed-panel-head">
        <div>
          <div className="ed-panel-title">작업 큐</div>
          <div className="ed-panel-meta">{tasks.length}건 접수</div>
        </div>
        <span className="ed-tag ed-tag--warn">QUEUE</span>
      </div>
      <p className="ed-hint" style={{ marginTop: 12 }}>
        항목을 선택하면 위 분석 결과 영역에서 해당 종목의 최신 상태와 결과를 확인할 수 있습니다.
      </p>
      <div className="ed-task-list">
        {tasks.map((item) => {
          const stockLabel = item.message.replace(/\s*analysis queued\s*$/i, "");
          const active = activeTaskId === item.taskId;
          return (
            <button
              type="button"
              className={`ed-task-item${active ? " ed-task-item--on" : ""}`}
              key={item.taskId}
              onClick={() => onTrackTask(item)}
            >
              <span className="ed-task-badge">
                {active ? "선택" : "AI"}
              </span>
              <span className="ed-row-main">
                <span className="ed-row-name">{stockLabel}</span>
                <span className="ed-row-meta">
                  {item.taskId.slice(0, 8)}
                  {item.estimatedTimeSeconds != null ? ` · 예상 ${Math.ceil(item.estimatedTimeSeconds / 60)}분` : ""}
                </span>
              </span>
              <span className={`ed-tag ed-tag--${analysisStatusTone(item.status)}`}>
                {translateAnalysisStatus(item.status)}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function translateAnalysisStatus(status?: string | null) {
  switch (status) {
    case "pending": return "대기 중";
    case "started": return "진행 중";
    case "running": return "진행 중";
    case "completed": return "완료";
    case "failed": return "실패";
    case "error": return "실패";
    default: return status ?? "-";
  }
}

function analysisStatusTone(status?: string | null): "good" | "warn" | "bad" {
  if (status === "completed") return "good";
  if (status === "failed" || status === "error") return "bad";
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
  const taskLabel = task?.message.replace(/\s*analysis queued\s*$/i, "");
  const modeLabel = result?.mode === "quick" ? "빠른 분석" : "공통 종목 분석";

  return (
    <section className="ed-console-panel">
      <div className="ed-live-head">
        <div>
          <p className="ed-live-name">{taskLabel ?? result?.stock.name ?? "분석 결과"}</p>
          <p className="ed-live-id">
            {modeLabel}
            {task?.taskId ? ` · ${task.taskId.slice(0, 8)}` : ""}
          </p>
        </div>
        <span className={`ed-tag ed-tag--${analysisStatusTone(status)}`}>
          {translateAnalysisStatus(status)}
        </span>
      </div>

      {!isFinished && progress ? (
        <div className="ed-live-progress">
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

      {result ? <AnalysisSummaryCard result={result} /> : null}
      {result?.scores?.length ? <AgentDetailSections scores={result.scores} /> : null}

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

      {status === "failed" && !result?.scores?.length && !result?.errors && !error ? (
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
  explanations,
  explanationsLoading,
  onRefresh,
  onSelectStock
}: {
  loading: boolean;
  error: string;
  data: Record<string, unknown> | null;
  explanations: AutoTradeExplanation[];
  explanationsLoading: boolean;
  onRefresh: () => void;
  onSelectStock: (code: string) => void;
}) {
  const orders = extractOrders(data);
  const [view, setView] = useState<"rationale" | "orders">("rationale");
  return (
    <>
      <div className="ed-app-head">
        <div className="ed-kicker">거래 내역</div>
        <h1 className="ed-app-h">거래와 AI 판단</h1>
      </div>

      <div className="ed-sec-head">
        <div className="ed-seg">
          <button
            type="button"
            className={`ed-seg-btn${view === "rationale" ? " ed-seg-btn--on" : ""}`}
            onClick={() => setView("rationale")}
          >
            AI 매매근거
          </button>
          <button
            type="button"
            className={`ed-seg-btn${view === "orders" ? " ed-seg-btn--on" : ""}`}
            onClick={() => setView("orders")}
          >
            주문 내역
          </button>
        </div>
        <button type="button" className="ed-btn ed-btn--line ed-btn--sm" onClick={onRefresh} disabled={loading}>
          {loading ? "불러오는 중..." : "새로고침"}
        </button>
      </div>

      {view === "rationale" ? (
        <AutoTradeExplanationSection
          items={explanations}
          loading={explanationsLoading}
          onSelectStock={onSelectStock}
        />
      ) : (
        <section className="ed-sec">
          <div className="ed-sec-head">
            <span className="ed-sec-title">주문 내역</span>
            <span className="ed-sec-meta">{loading ? "불러오는 중" : `${orders.length}건`}</span>
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
        </section>
      )}
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
  balance,
  balanceLoading,
  balanceError,
  totalAssetsText,
  monthlyInvestmentText,
  onGoKis,
  onGoPreference
}: {
  preference: UserPreference | null;
  user: AuthUser | null;
  balance: Balance | null;
  balanceLoading: boolean;
  balanceError: string;
  totalAssetsText: string;
  monthlyInvestmentText: string;
  onGoKis: () => void;
  onGoPreference: () => void;
}) {
  const summary = balance?.summary;
  const kisLinked = !!user?.kisConfigured;
  const totalAssetDisplay = kisLinked
    ? balanceLoading ? "불러오는 중..." : summary?.totalEvalAmount != null ? formatPrice(summary.totalEvalAmount) : "-"
    : totalAssetsText;

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
          <small>{kisLinked ? "연결된 KIS 계좌 잔고" : "설문에 입력한 자산"}</small>
          <b>{totalAssetDisplay}</b>
        </div>
        {kisLinked ? (
          <>
            <div className="ed-fig ed-fig--md">
              <small>예수금</small>
              <b>{summary?.deposit != null ? formatPrice(summary.deposit) : "-"}</b>
            </div>
            <div className="ed-fig ed-fig--md">
              <small>주식 평가</small>
              <b>{summary?.stockEvalAmount != null ? formatPrice(summary.stockEvalAmount) : "-"}</b>
            </div>
          </>
        ) : (
          <div className="ed-fig ed-fig--md">
            <small>월 투자 금액</small>
            <b>{monthlyInvestmentText}</b>
          </div>
        )}
      </div>

      {kisLinked && balanceError ? <p className="ed-msg">{balanceError}</p> : null}

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
          {kisLinked
            ? "연결된 KIS 계좌에서 조회한 잔고를 표시합니다."
            : "설문 입력값입니다. 증권 계좌 잔고는 KIS 계좌 연결 후 조회할 수 있습니다."}
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
  balance: Balance | null;
  balanceLoading: boolean;
  balanceError: string;
  ordersData: Record<string, unknown> | null;
  ordersLoading: boolean;
  autoTradeEnabled: boolean;
  recentAnalyses: AnalysisHistoryItem[];
  recentAnalysesLoading: boolean;
  aiActivity: AiActivityResponse | null;
  aiActivityLoading: boolean;
  autoTradeExplanations: AutoTradeExplanation[];
  autoTradeExplanationsLoading: boolean;
  indices: MarketIndex[];
  onRefresh: () => void;
  onGoTab: (t: WorkspaceTab) => void;
  onGoKis: () => void;
  onGoBacktest: () => void;
  onSelectStock: (code: string) => void;
}) {
  const {
    user, balance, balanceLoading, balanceError,
    ordersData, autoTradeEnabled, recentAnalyses, recentAnalysesLoading, aiActivity, aiActivityLoading,
    autoTradeExplanations, autoTradeExplanationsLoading, indices,
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
  const [expandedAnalysisId, setExpandedAnalysisId] = useState<string | null>(null);
  const [analysisDetails, setAnalysisDetails] = useState<Record<string, AnalysisResult>>({});
  const [analysisDetailLoading, setAnalysisDetailLoading] = useState<string | null>(null);

  return (
    <div className="workspace-overview">
      <div className="ed-app-head">
        <div className="ed-kicker">MY INVESTMENT SPACE / PAPER</div>
        <h1 className="ed-app-h">
          {user ? `${user.firstName}님, 환영합니다` : "환영합니다"}
        </h1>
        <p className="ed-greet" style={{ marginTop: 6 }}>
          {autoTradeEnabled
            ? "모의 자동매매가 켜져 있어요. 최근 판단과 주문 상태를 확인하세요."
            : "오늘의 관심 종목부터, 차근차근 살펴볼까요?"}
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

      <section className="workspace-welcome">
        <div><p className="luna-eyebrow">A FRESH PERSPECTIVE</p><h2>투자의 다음 장, 근거부터 읽어보세요.</h2><p>공시를 읽는 Analyst, 숫자를 보는 Quant, 흐름을 찾는 Chartist.<br />세 전문가의 시선을 모아 나의 판단을 더 선명하게.</p>
        <button type="button" className="ed-btn ed-btn--moss ed-btn--sm" style={{ marginTop: 16 }} onClick={() => onGoTab("analysis")}>AI 리서치 시작하기 ↗</button></div>
        <span className="workspace-welcome-art" aria-hidden="true">✳</span>
      </section>
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
            모의투자 계좌를 연결하면 평가금액과 보유 종목을 확인할 수 있어요.
            <div style={{ marginTop: 10 }}>
              <button type="button" className="ed-btn ed-btn--moss ed-btn--sm" onClick={onGoKis}>
                KIS 계좌 연결
              </button>
            </div>
          </div>
        ) : balanceLoading ? (
          <p className="ed-hint" style={{ marginTop: 14 }}>잔고를 불러오는 중...</p>
        ) : balanceError ? (
          <p className="ed-msg" style={{ borderLeftColor: "var(--up)", marginTop: 14 }}>
            {balanceError}
          </p>
        ) : (
          <div className="ed-figrow" style={{ marginTop: 14 }}>
            <div className="ed-fig ed-fig--xl">
              <small>총 평가자산</small>
              <b>
                {totalEval != null ? formatPrice(totalEval) : "-"}
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

      {/* AI 운용 요약 — multi-theme 주도주 선별 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">AI 운용 요약</span>
          <span className="ed-sec-meta">
            {aiActivity?.executedAt ? aiActivity.executedAt : aiActivityLoading ? "불러오는 중" : aiActivity?.bestTheme ?? ""}
          </span>
        </div>
        {aiActivityLoading && !aiActivity ? (
          <p className="ed-hint" style={{ padding: "16px 4px" }}>AI 운용 데이터를 불러오는 중...</p>
        ) : !aiActivity?.leaders?.length ? (
          <p className="ed-hint" style={{ padding: "16px 4px" }}>
            아직 표시할 AI 운용 결과가 없습니다.
          </p>
        ) : (
          <>
            <div className="ed-figrow" style={{ marginTop: 14 }}>
              <div className="ed-fig ed-fig--md">
                <small>최우선 테마</small>
                <b>{aiActivity.bestTheme || "-"}</b>
              </div>
              <div className="ed-fig ed-fig--md">
                <small>분석 테마</small>
                <b>{aiActivity.themeCount ?? "-"}개</b>
              </div>
              <div className="ed-fig ed-fig--md">
                <small>선별 종목</small>
                <b>{aiActivity.leaderCount ?? aiActivity.leaders.length}개</b>
              </div>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                gap: 10,
                marginTop: 16
              }}
            >
              {aiActivity.leaders.slice(0, 6).map((leader) => {
                const actionTone = actionToneOf(leader.action);
                const returnPct = typeof leader.returnPct === "number" ? leader.returnPct : null;
                return (
                  <button
                    type="button"
                    className="ed-scard"
                    key={`${leader.stockCode}-${leader.rank}`}
                    style={{ textAlign: "left", cursor: "pointer" }}
                    onClick={() => onSelectStock(leader.stockCode)}
                  >
                    <div className="ed-scard-head">
                      <span className="ed-scard-name">{leader.stockName}</span>
                      <span className="ed-tag" style={{ background: actionTone.bg, color: actionTone.fg }}>
                        {leader.action || "-"}
                      </span>
                    </div>
                    <p className="ed-scard-score" style={{ color: "var(--moss)", fontWeight: 800 }}>
                      {leader.score == null ? "점수 없음" : `${leader.score}점`} · 신뢰도 {leader.confidence == null ? "없음" : `${leader.confidence}%`}
                      {returnPct != null ? ` · 수익률 ${formatSignedRate(returnPct)}` : ""}
                    </p>
                    <p className="ed-scard-text">
                      {leader.theme} · {leader.stockCode} · 위험 {leader.riskLevel || "-"}
                    </p>
                    <p className="ed-scard-text" style={{ marginTop: 8 }}>
                      {leader.summary || leader.analystSummary || "요약 없음"}
                    </p>
                    {leader.catalysts?.length ? (
                      <p className="ed-scard-text" style={{ marginTop: 8, color: "var(--ink-2)" }}>
                        촉매: {leader.catalysts.slice(0, 2).join(" · ")}
                      </p>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </>
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
              최근 {recentAnalyses.length}건의 분석 결과 — 전문가별 근거와 데이터 품질을 함께 확인하세요.
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
                const detail = analysisDetails[a.taskId];
                const expanded = expandedAnalysisId === a.taskId;
                return (
                  <div
                    key={a.taskId}
                    className="ed-scard"
                    style={{
                      textAlign: "left",
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
                    <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                      <button
                        type="button"
                        className="ed-btn ed-btn--line ed-btn--sm"
                        onClick={() => {
                          if (expanded) {
                            setExpandedAnalysisId(null);
                            return;
                          }
                          setExpandedAnalysisId(a.taskId);
                          if (!analysisDetails[a.taskId]) {
                            setAnalysisDetailLoading(a.taskId);
                            analysisApi.result(a.taskId)
                              .then((res) => setAnalysisDetails((prev) => ({ ...prev, [a.taskId]: res })))
                              .catch(() => { /* 상세 조회 실패 시 카드 목록은 유지 */ })
                              .finally(() => setAnalysisDetailLoading(null));
                          }
                        }}
                      >
                        {expanded ? "접기" : "상세"}
                      </button>
                      <button
                        type="button"
                        className="ed-btn ed-btn--line ed-btn--sm"
                        onClick={() => onSelectStock(a.stock.code)}
                      >
                        종목 보기
                      </button>
                    </div>
                    {expanded && analysisDetailLoading === a.taskId ? (
                      <p className="ed-hint" style={{ marginTop: 12 }}>상세 분석을 불러오는 중...</p>
                    ) : null}
                    {expanded && detail ? (
                      <>
                        <AnalysisSummaryCard result={detail} />
                        <AgentDetailSections scores={detail.scores} />
                      </>
                    ) : null}
                  </div>
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
    </div>
  );
}

function AutoTradeExplanationSection({
  items,
  loading,
  onSelectStock
}: {
  items: AutoTradeExplanation[];
  loading: boolean;
  onSelectStock: (code: string) => void;
}) {
  return (
    <section className="ed-sec">
      <div className="ed-sec-head">
        <span className="ed-sec-title">AI 매매 근거</span>
        <span className="ed-sec-meta">{loading ? "불러오는 중" : `${items.length}건`}</span>
      </div>
      {loading && items.length === 0 ? (
        <p className="ed-hint" style={{ padding: "16px 4px" }}>최근 자동매매 판단 근거를 불러오는 중...</p>
      ) : items.length === 0 ? (
        <p className="ed-hint" style={{ padding: "16px 4px" }}>
          아직 표시할 자동매매 판단 근거가 없습니다. 자동매매 신호가 생성되면 여기에 판단 이유와 주문 결과가 표시됩니다.
        </p>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
            gap: 12,
            marginTop: 14
          }}
        >
          {items.slice(0, 3).map((item) => {
            const actionTone = actionToneOf(item.action);
            const status = item.executionStatus ?? item.status;
            const blockedReason = item.executionRejectReason ?? item.rejectReason;
            return (
              <article
                className="ed-scard"
                key={item.signalId}
                style={{ textAlign: "left", borderLeft: `3px solid ${actionTone.fg}` }}
              >
                <div className="ed-scard-head">
                  <span className="ed-scard-name">{item.stockName}</span>
                  <span className="ed-tag" style={{ background: actionTone.bg, color: actionTone.fg }}>
                    {item.action || "-"}
                  </span>
                </div>
                <p className="ed-scard-score" style={{ color: "var(--moss)", fontWeight: 800 }}>
                  신뢰도 {item.confidence}% · 리스크 {item.riskLevel || "-"}
                  {item.positionSize ? ` · 비중 ${item.positionSize}` : ""}
                </p>
                <p className="ed-scard-text" style={{ marginTop: 8 }}>
                  {item.explanationSummary || item.reason || "최종 판단 근거가 아직 저장되지 않았습니다."}
                </p>

                <div className="ed-pillbar" style={{ marginTop: 12 }}>
                  <span className={`ed-pill${status === "EXECUTED" ? " ed-pill--live" : ""}`}>
                    주문 {translateTradeStatus(status)}
                  </span>
                  {item.signalPrice != null ? <span className="ed-pill">판단가 {formatPrice(item.signalPrice)}</span> : null}
                  {item.currentPrice != null ? <span className="ed-pill">현재가 {formatPrice(item.currentPrice)}</span> : null}
                  {item.priceDriftPct != null ? <span className="ed-pill">괴리 {item.priceDriftPct.toFixed(2)}%</span> : null}
                </div>

                {blockedReason ? (
                  <p className="ed-msg" style={{ marginTop: 12, borderLeftColor: "var(--spark)" }}>
                    주문 제한 사유: {translateRejectReason(blockedReason)}
                  </p>
                ) : null}

                {item.catalysts.length || item.risks.length ? (
                  <div style={{ display: "grid", gap: 6, marginTop: 12 }}>
                    {item.catalysts.length ? (
                      <p className="ed-scard-text" style={{ color: "var(--ink-2)" }}>
                        긍정 근거: {item.catalysts.slice(0, 2).join(" · ")}
                      </p>
                    ) : null}
                    {item.risks.length ? (
                      <p className="ed-scard-text" style={{ color: "var(--ink-2)" }}>
                        주의 근거: {item.risks.slice(0, 2).join(" · ")}
                      </p>
                    ) : null}
                  </div>
                ) : null}

                {item.agentReasons.length ? (
                  <div style={{ marginTop: 14, display: "grid", gap: 7 }}>
                    {item.agentReasons.slice(0, 4).map((reason) => (
                      <div
                        key={`${item.signalId}-${reason.agent}`}
                        style={{
                          display: "grid",
                          gridTemplateColumns: "86px minmax(0, 1fr)",
                          gap: 9,
                          paddingTop: 8,
                          borderTop: "1px solid var(--rule)"
                        }}
                      >
                        <span style={{ color: "var(--ink-2)", fontSize: ".76rem", fontWeight: 800 }}>
                          {reason.label}
                        </span>
                        <span style={{ minWidth: 0 }}>
                          <span className="ed-scard-text" style={{ display: "block" }}>
                            {reason.verdict ? `${reason.verdict} · ` : ""}
                            {reason.score != null ? `${reason.score}점 · ` : ""}
                            {reason.summary || "요약 없음"}
                          </span>
                        </span>
                      </div>
                    ))}
                  </div>
                ) : null}

                <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="ed-btn ed-btn--line ed-btn--sm"
                    onClick={() => onSelectStock(item.stockCode)}
                  >
                    종목 보기
                  </button>
                  <span className="ed-hint" style={{ alignSelf: "center", fontSize: ".76rem" }}>
                    {formatTimeAgo(item.executedAt ?? item.updatedAt ?? item.createdAt)}
                  </span>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function translateTradeStatus(status?: string | null) {
  switch (status) {
    case "PENDING": return "대기";
    case "EXECUTED": return "실행됨";
    case "REJECTED": return "보류";
    case "FAILED": return "실패";
    case "EXPIRED": return "만료";
    default: return status ?? "-";
  }
}

function translateRejectReason(reason?: string | null) {
  switch (reason) {
    case "AUTO_TRADE_DISABLED": return "자동매매가 꺼져 있어 주문하지 않았습니다.";
    case "KIS_SECRET_MISSING": return "KIS API 키 또는 계좌 정보가 없어 주문하지 않았습니다.";
    case "KIS_TOKEN_UNAVAILABLE": return "KIS 토큰 발급에 실패했습니다.";
    case "KIS_BALANCE_UNAVAILABLE": return "계좌 잔고를 확인하지 못했습니다.";
    case "CURRENT_PRICE_UNAVAILABLE": return "현재가를 확인하지 못했습니다.";
    case "PRICE_DRIFT_EXCEEDED": return "AI 판단 시점 가격과 주문 시점 가격 차이가 안전 기준을 넘었습니다.";
    case "INVALID_ORDER_QUANTITY": return "주문 가능 수량이 0이라 주문하지 않았습니다.";
    case "NO_SELLABLE_HOLDING": return "매도 가능한 보유 수량이 없습니다.";
    case "INSUFFICIENT_CASH": return "주문 가능 현금이 부족합니다.";
    case "KIS_ORDER_FAILED": return "KIS 주문 요청이 실패했습니다.";
    case "SIGNAL_EXPIRED": return "매매 신호 유효 시간이 지나 주문하지 않았습니다.";
    default: return reason ?? "-";
  }
}
