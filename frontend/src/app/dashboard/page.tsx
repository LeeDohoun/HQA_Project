"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { analysisApi, authApi, chartApi, stockApi, tradingApi } from "@/lib/api";
import { StatusPill } from "@/components/common/status-pill";
import { TradingViewChart } from "@/components/common/tradingview-chart";
import type {
  AnalysisMode,
  AnalysisTaskResponse,
  AuthUser,
  Candle,
  RealtimePrice,
  StockSearchResult,
  UserPreference
} from "@/types/api";

type WorkspaceTab = "analysis" | "order";
type OrderSide = "buy" | "sell";
type ChartTimeframe = "1d" | "1w" | "1M";

const TIMEFRAME_TABS: Array<{ value: ChartTimeframe; label: string }> = [
  { value: "1d", label: "일봉" },
  { value: "1w", label: "주봉" },
  { value: "1M", label: "월봉" }
];

// timeframe별로 한 번에 가져올 캔들 개수. 봉이 굵을수록 줄임.
const TIMEFRAME_COUNT: Record<ChartTimeframe, number> = {
  "1d": 200,
  "1w": 120,
  "1M": 60
};

const RECENT_STORAGE_KEY = "hqa.dashboard.recent";
const RECENT_LIMIT = 8;

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

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [preference, setPreference] = useState<UserPreference | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([]);
  const [recent, setRecent] = useState<StockSearchResult[]>([]);
  const [selected, setSelected] = useState<StockSearchResult | null>(null);
  const [mode, setMode] = useState<AnalysisMode>("full");
  const [tab, setTab] = useState<WorkspaceTab>("analysis");
  const [message, setMessage] = useState("");
  const [loadingUser, setLoadingUser] = useState(true);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<AnalysisTaskResponse | null>(null);
  const [autoTradeEnabled, setAutoTradeEnabled] = useState(false);
  const [bulkAnalyzing, setBulkAnalyzing] = useState(false);
  const [buyQuantity, setBuyQuantity] = useState("1");
  const [buyPrice, setBuyPrice] = useState("");
  const [orderSide, setOrderSide] = useState<OrderSide>("buy");
  const [timeframe, setTimeframe] = useState<ChartTimeframe>("1d");
  const [price, setPrice] = useState<RealtimePrice | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loadingChart, setLoadingChart] = useState(false);
  const [chartError, setChartError] = useState("");
  // 페이지네이션 상태: 더 가져올 게 있는지 + 동시에 두 번 안 부르도록 잠금.
  const [hasMoreCandles, setHasMoreCandles] = useState(false);
  const loadingMoreRef = useRef(false);
  // (종목, timeframe)이 바뀌면 진행 중이던 prepend의 결과를 버리기 위한 토큰.
  const seriesTokenRef = useRef(0);

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

  useEffect(() => {
    let active = true;
    // 종목/timeframe이 바뀔 때마다 토큰을 증가시켜, 진행 중이던 prepend의 결과를 무시할 수 있게 한다.
    const myToken = ++seriesTokenRef.current;
    // 새 시드 로드 시작 — 진행 중인 prepend 잠금을 해제하고 hasMore도 초기화.
    loadingMoreRef.current = false;
    setHasMoreCandles(false);

    async function loadMarketData() {
      if (!selected) {
        setPrice(null);
        setCandles([]);
        setChartError("");
        return;
      }

      setLoadingChart(true);
      setChartError("");

      try {
        const [priceResponse, candleResponse] = await Promise.all([
          stockApi.price(selected.code),
          chartApi.history(selected.code, timeframe, TIMEFRAME_COUNT[timeframe])
        ]);

        if (!active || seriesTokenRef.current !== myToken) return;
        setPrice(priceResponse);
        setCandles(candleResponse.candles);
        setHasMoreCandles(candleResponse.hasMore);
      } catch (error) {
        if (!active || seriesTokenRef.current !== myToken) return;
        setPrice(null);
        setCandles([]);
        setHasMoreCandles(false);
        setChartError(error instanceof Error ? error.message : "차트 데이터를 불러오지 못했습니다.");
      } finally {
        if (active && seriesTokenRef.current === myToken) setLoadingChart(false);
      }
    }

    void loadMarketData();
    return () => { active = false; };
  }, [selected, timeframe]);

  // 과거 봉을 페이지네이션으로 prepend.
  // 호출 조건은 TradingViewChart가 결정 (좌측 절반 진입). 여기서는 has_more / 잠금만 확인.
  const loadMoreCandles = useCallback(async () => {
    if (!selected) return;
    if (loadingMoreRef.current) return;
    if (!hasMoreCandles) return;
    if (candles.length === 0) return;

    const myToken = seriesTokenRef.current;
    const before = candles[0].time; // 가장 오래된 캔들 이전을 요청
    const currentSelected = selected;
    const currentTimeframe = timeframe;

    loadingMoreRef.current = true;
    try {
      const response = await chartApi.history(
        currentSelected.code,
        currentTimeframe,
        TIMEFRAME_COUNT[currentTimeframe],
        before
      );
      // 응답이 돌아오는 동안 종목/timeframe이 바뀌었으면 결과 폐기.
      if (seriesTokenRef.current !== myToken) return;
      if (response.candles.length === 0) {
        setHasMoreCandles(false);
        return;
      }
      setCandles((prev) => {
        // 중복 제거: 새 페이지가 기존 가장 오래된 봉 시각 이상의 항목을 포함할 수 있음 (서버 페이지 경계).
        const oldestExisting = prev[0]?.time ?? Number.POSITIVE_INFINITY;
        const merged = [
          ...response.candles.filter((c) => c.time < oldestExisting),
          ...prev
        ];
        return merged;
      });
      setHasMoreCandles(response.hasMore);
    } catch {
      // prepend 실패는 silently swallow — 차트 자체는 사용 가능해야 함.
      // 다음 트리거 때 다시 시도되도록 hasMore는 그대로 둔다.
    } finally {
      if (seriesTokenRef.current === myToken) {
        loadingMoreRef.current = false;
      }
    }
  }, [selected, timeframe, hasMoreCandles, candles]);

  function pickStock(stock: StockSearchResult) {
    setSelected(stock);
    setRecent((prev) => {
      const deduped = prev.filter((s) => s.code !== stock.code);
      const next = [stock, ...deduped].slice(0, RECENT_LIMIT);
      saveRecent(next);
      return next;
    });
  }

  async function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!searchQuery.trim()) return;

    setSearching(true);
    setMessage("");

    try {
      const response = await stockApi.search(searchQuery.trim());
      setSearchResults(response.results);
      if (response.results[0]) {
        pickStock(response.results[0]);
      }
      if (response.results.length === 0) setMessage("검색 결과가 없습니다.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "종목 검색에 실패했습니다.");
    } finally {
      setSearching(false);
    }
  }

  async function submitAnalysis() {
    if (!selected) {
      setMessage("종목을 먼저 선택해주세요.");
      return;
    }

    setSubmitting(true);
    setMessage("");

    try {
      const response = await analysisApi.submit({
        stockName: selected.name,
        stockCode: selected.code,
        mode,
        maxRetries: mode === "full" ? 1 : 0
      });
      setTask(response);
      router.push(`/analysis/${response.taskId}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "분석 요청에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOrder() {
    if (!selected) {
      setMessage("종목을 먼저 선택해주세요.");
      return;
    }
    const qty = Math.max(1, parseInt(buyQuantity || "1", 10) || 1);
    const orderPrice = Math.max(0, parseInt(buyPrice || "0", 10) || 0);
    const priceLabel = orderPrice > 0 ? `${orderPrice.toLocaleString("ko-KR")}원` : "시장가";
    const sideLabel = orderSide === "buy" ? "매수" : "매도";
    const confirmed = window.confirm(`${selected.name} ${qty}주를 ${priceLabel}로 ${sideLabel}할까요?`);
    if (!confirmed) return;
    try {
      const payload = {
        stockName: selected.name,
        stockCode: selected.code,
        quantity: qty,
        limitPrice: orderPrice
      };
      const result =
        orderSide === "buy"
          ? await tradingApi.buy(payload)
          : await tradingApi.sell(payload);
      if (result.success) {
        setMessage(`${selected.name} ${qty}주 ${sideLabel} 주문이 접수되었습니다.`);
      } else {
        const reason = result.error
          ?? (typeof result.response?.msg1 === "string" ? (result.response.msg1 as string) : `${sideLabel} 주문이 거부되었습니다.`);
        setMessage(`${sideLabel} 실패: ${reason}`);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `${sideLabel} 요청에 실패했습니다.`);
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

  const candleStats = useMemo(() => {
    if (candles.length === 0) return null;
    return candles[candles.length - 1];
  }, [candles]);

  const priceChange = price?.change ?? 0;
  const pricePositive = priceChange >= 0;

  if (loadingUser) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg)" }}>
        <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>로딩 중...</p>
      </div>
    );
  }

  return (
    <div className="dashboard-shell theme-dark">
      {/* ── Topbar ── */}
      <header className="topbar">
        <div className="topbar-left">
          <div className="brand-chip">HQA</div>
          <span className="topbar-greet">
            {user ? `${user.firstName}님, 환영해요` : ""}
          </span>
        </div>

        <div className="topbar-actions">
          <button
            className="button-ghost"
            onClick={handleBulkAnalyze}
            type="button"
            disabled={bulkAnalyzing}
          >
            {bulkAnalyzing ? "요청 중..." : "전체 분석"}
          </button>
          <button
            className={autoTradeEnabled ? "button-secondary" : "button-ghost"}
            onClick={handleAutoTrade}
            type="button"
          >
            자동매매 {autoTradeEnabled ? "ON" : "OFF"}
          </button>
          <Link className="button-ghost" href="/settings/kis">
            KIS 설정
          </Link>
          <Link className="button-ghost" href="/onboarding/preference">
            투자 성향
          </Link>
          <button className="button-ghost" onClick={logout} type="button">
            로그아웃
          </button>
        </div>
      </header>

      {/* ── Account Strip (2 cards now) ── */}
      <div className="account-strip">
        <div className="account-card">
          <span className="account-label">보유 자산</span>
          <span className="account-value">{totalAssetsText}</span>
        </div>
        <div className="account-card">
          <span className="account-label">월 투자 금액</span>
          <span className="account-value">{monthlyInvestmentText}</span>
        </div>
      </div>

      {/* ── Main: 3-column workspace ── */}
      <div className="dashboard-main">
        {/* ── LEFT: Search + Watchlist + Results ── */}
        <aside className="sidebar">
          <div className="sidebar-section">
            <form className="search-form" onSubmit={onSearch}>
              <input
                className="search-input"
                placeholder="종목명·코드 검색"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
              />
              <button className="button" disabled={searching} type="submit" style={{ flexShrink: 0 }}>
                {searching ? "..." : "검색"}
              </button>
            </form>
          </div>

          <div className="sidebar-section sidebar-scroll">
            {searchResults.length > 0 ? (
              <>
                <p className="sidebar-heading">검색 결과 ({searchResults.length})</p>
                <div className="result-list">
                  {searchResults.map((item) => (
                    <button
                      key={`s-${item.code}-${item.market}`}
                      className={selected?.code === item.code ? "result-item active" : "result-item"}
                      onClick={() => pickStock(item)}
                      type="button"
                    >
                      <div>
                        <div className="result-name">{item.name}</div>
                        <div className="result-code">{item.code} · {item.market}</div>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            ) : null}

            {recent.length > 0 ? (
              <>
                <p className="sidebar-heading" style={{ marginTop: searchResults.length > 0 ? 16 : 0 }}>
                  최근 본 종목
                </p>
                <div className="result-list">
                  {recent.map((item) => (
                    <button
                      key={`r-${item.code}-${item.market}`}
                      className={selected?.code === item.code ? "result-item active" : "result-item"}
                      onClick={() => pickStock(item)}
                      type="button"
                    >
                      <div>
                        <div className="result-name">{item.name}</div>
                        <div className="result-code">{item.code} · {item.market}</div>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            ) : null}

            {searchResults.length === 0 && recent.length === 0 ? (
              <div className="sidebar-empty">
                <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--text-bright)", fontWeight: 600 }}>
                  종목을 검색해보세요
                </p>
                <p style={{ margin: "6px 0 0", fontSize: "0.78rem", color: "var(--muted)", lineHeight: 1.5 }}>
                  종목명 또는 6자리 코드로 검색할 수 있어요.<br />
                  최근 본 종목은 여기에 모입니다.
                </p>
              </div>
            ) : null}
          </div>
        </aside>

        {/* ── CENTER: Chart hero ── */}
        <main className="chart-pane">
          {selected ? (
            <>
              <div className="chart-pane-head">
                <div>
                  <h1 className="chart-pane-name">{selected.name}</h1>
                  <p className="chart-pane-code mono">{selected.code} · {selected.market}</p>
                </div>
                <div className="timeframe-switch">
                  {TIMEFRAME_TABS.map((tab) => (
                    <button
                      key={tab.value}
                      className={timeframe === tab.value ? "tab-chip active" : "tab-chip"}
                      onClick={() => setTimeframe(tab.value)}
                      type="button"
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="price-strip">
                <span className="price-main">{formatPrice(price?.currentPrice)}</span>
                <span className={`price-delta ${pricePositive ? "positive" : "negative"}`}>
                  {formatSignedNumber(price?.change)}원
                </span>
                <span className={`price-rate ${pricePositive ? "positive" : "negative"}`}>
                  {formatSignedRate(price?.changeRate)}
                </span>
              </div>

              <div className="chart-canvas chart-canvas-large">
                {loadingChart ? <div className="chart-empty">로딩 중...</div> : null}
                {!loadingChart && chartError ? <div className="chart-empty">{chartError}</div> : null}
                {!loadingChart && !chartError && candles.length === 0 ? (
                  <div className="chart-empty">차트 데이터 없음</div>
                ) : null}
                {!loadingChart && !chartError && candles.length > 0 ? (
                  <TradingViewChart
                    candles={candles}
                    timeframe={timeframe}
                    onScrolledPastHalfLeft={loadMoreCandles}
                  />
                ) : null}
              </div>

              <div className="quote-grid">
                <div className="quote-card">
                  <span className="quote-label">시가</span>
                  <span className="quote-value">{formatPrice(candleStats?.open ?? price?.openPrice)}</span>
                </div>
                <div className="quote-card">
                  <span className="quote-label">고가</span>
                  <span className="quote-value">{formatPrice(candleStats?.high ?? price?.highPrice)}</span>
                </div>
                <div className="quote-card">
                  <span className="quote-label">저가</span>
                  <span className="quote-value">{formatPrice(candleStats?.low ?? price?.lowPrice)}</span>
                </div>
                <div className="quote-card">
                  <span className="quote-label">거래량</span>
                  <span className="quote-value mono">{formatNumber(candleStats?.volume ?? price?.volume)}</span>
                </div>
              </div>
            </>
          ) : (
            <div className="empty-hero">
              <span className="empty-hero-emoji" aria-hidden>📊</span>
              <h1 className="empty-hero-title">분석할 종목을 선택해주세요</h1>
              <p className="empty-hero-text">
                왼쪽에서 종목을 검색하면<br />
                실시간 가격과 차트를 볼 수 있고<br />
                바로 AI 분석을 시작할 수 있어요.
              </p>
              <div className="empty-hero-tips">
                <div className="empty-hero-tip">
                  <span className="empty-hero-tip-num">1</span>
                  <span>왼쪽에서 종목 검색</span>
                </div>
                <div className="empty-hero-tip">
                  <span className="empty-hero-tip-num">2</span>
                  <span>차트와 가격 확인</span>
                </div>
                <div className="empty-hero-tip">
                  <span className="empty-hero-tip-num">3</span>
                  <span>AI 분석 또는 매수</span>
                </div>
              </div>
            </div>
          )}
        </main>

        {/* ── RIGHT: Actions (AI Analysis / Order) ── */}
        <aside className="action-pane">
          <div className="tab-switch action-tabs">
            <button
              className={tab === "analysis" ? "tab-chip active" : "tab-chip"}
              onClick={() => setTab("analysis")}
              type="button"
            >
              AI 분석
            </button>
            <button
              className={tab === "order" ? "tab-chip active" : "tab-chip"}
              onClick={() => setTab("order")}
              type="button"
            >
              주문
            </button>
          </div>

          {tab === "analysis" ? (
            <div className="action-content">
              <p className="action-hint">
                {selected
                  ? `${selected.name}을(를) AI가 분석해드려요.`
                  : "종목을 선택하면 분석을 시작할 수 있어요."}
              </p>

              <div className="field">
                <label>분석 모드</label>
                <div className="mode-row">
                  <label className={mode === "full" ? "mode-chip active" : "mode-chip"}>
                    <input checked={mode === "full"} name="mode" type="radio" onChange={() => setMode("full")} />
                    전체 분석
                  </label>
                  <label className={mode === "quick" ? "mode-chip active" : "mode-chip"}>
                    <input checked={mode === "quick"} name="mode" type="radio" onChange={() => setMode("quick")} />
                    빠른 분석
                  </label>
                </div>
              </div>

              <button
                className="button action-cta"
                disabled={!selected || submitting}
                onClick={submitAnalysis}
                type="button"
              >
                {submitting ? "요청 중..." : "분석 시작"}
              </button>

              {task ? (
                <div className="summary-card">
                  <div className="summary-row">
                    <span className="summary-label">최근 작업</span>
                    <StatusPill label={task.status} tone="warn" />
                  </div>
                  <div className="summary-row">
                    <span className="summary-label">작업 ID</span>
                    <span className="summary-value mono" style={{ fontSize: "0.75rem" }}>
                      {task.taskId.slice(0, 12)}...
                    </span>
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="action-content">
              {/* Sell / Buy side toggle */}
              <div className="side-toggle" role="tablist" aria-label="주문 종류">
                <button
                  type="button"
                  role="tab"
                  aria-selected={orderSide === "sell"}
                  className={`side-toggle-btn sell ${orderSide === "sell" ? "active" : ""}`}
                  onClick={() => setOrderSide("sell")}
                >
                  매도
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={orderSide === "buy"}
                  className={`side-toggle-btn buy ${orderSide === "buy" ? "active" : ""}`}
                  onClick={() => setOrderSide("buy")}
                >
                  매수
                </button>
              </div>

              <p className="action-hint">
                {selected
                  ? `${selected.name} ${orderSide === "buy" ? "매수" : "매도"} 주문을 넣어요.`
                  : `종목을 선택하면 ${orderSide === "buy" ? "매수" : "매도"}할 수 있어요.`}
              </p>

              <div className="field">
                <label htmlFor="buyQuantity">수량 (주)</label>
                <input
                  id="buyQuantity"
                  min="1"
                  type="number"
                  value={buyQuantity}
                  onChange={(event) => setBuyQuantity(event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="buyPrice">지정가 (원)</label>
                <input
                  id="buyPrice"
                  inputMode="numeric"
                  placeholder="비워두면 시장가"
                  value={buyPrice}
                  onChange={(event) => setBuyPrice(event.target.value)}
                />
              </div>

              <button
                className={`button action-cta order-cta ${orderSide}`}
                disabled={!selected}
                onClick={handleOrder}
                type="button"
              >
                {orderSide === "buy" ? "매수 주문" : "매도 주문"}
              </button>
            </div>
          )}

          {message ? <p className="message-line">{message}</p> : null}
        </aside>
      </div>
    </div>
  );
}
