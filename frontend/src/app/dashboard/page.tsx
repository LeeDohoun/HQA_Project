"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { analysisApi, authApi, chartApi, stockApi } from "@/lib/api";
import { StatusPill } from "@/components/common/status-pill";
import type {
  AnalysisMode,
  AnalysisTaskResponse,
  AuthUser,
  Candle,
  RealtimePrice,
  StockSearchResult,
  UserPreference
} from "@/types/api";

type WorkspaceTab = "analysis" | "buy";
type ChartTimeframe = "1m" | "10m";

const marketSnapshot = [
  { label: "KOSPI", value: "2,742.31", delta: "+0.82%", positive: true },
  { label: "KOSDAQ", value: "871.44", delta: "-0.14%", positive: false },
  { label: "USD/KRW", value: "1,351.20", delta: "+0.31%", positive: true }
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

function formatChartTime(time: number) {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(new Date(time));
}

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [preference, setPreference] = useState<UserPreference | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([]);
  const [selected, setSelected] = useState<StockSearchResult | null>(null);
  const [mode, setMode] = useState<AnalysisMode>("full");
  const [tab, setTab] = useState<WorkspaceTab>("analysis");
  const [message, setMessage] = useState("");
  const [loadingUser, setLoadingUser] = useState(true);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<AnalysisTaskResponse | null>(null);
  const [autoTradeEnabled, setAutoTradeEnabled] = useState(false);
  const [buyQuantity, setBuyQuantity] = useState("1");
  const [buyPrice, setBuyPrice] = useState("");
  const [timeframe, setTimeframe] = useState<ChartTimeframe>("1m");
  const [price, setPrice] = useState<RealtimePrice | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loadingChart, setLoadingChart] = useState(false);
  const [chartError, setChartError] = useState("");

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

    return () => { active = false; };
  }, [router]);

  useEffect(() => {
    let active = true;

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
          chartApi.history(selected.code, timeframe, timeframe === "1m" ? 120 : 80)
        ]);

        if (!active) return;
        setPrice(priceResponse);
        setCandles(candleResponse.candles);
      } catch (error) {
        if (!active) return;
        setPrice(null);
        setCandles([]);
        setChartError(error instanceof Error ? error.message : "차트 데이터를 불러오지 못했습니다.");
      } finally {
        if (active) setLoadingChart(false);
      }
    }

    void loadMarketData();
    return () => { active = false; };
  }, [selected, timeframe]);

  async function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!searchQuery.trim()) return;

    setSearching(true);
    setMessage("");

    try {
      const response = await stockApi.search(searchQuery.trim());
      setSearchResults(response.results);
      setSelected(response.results[0] ?? null);
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

  function handleBuy() {
    if (!selected) {
      setMessage("종목을 먼저 선택해주세요.");
      return;
    }
    const confirmed = window.confirm(`${selected.name} ${buyQuantity || "1"}주를 ${buyPrice || "시장가"}로 매수할까요?`);
    if (confirmed) setMessage("매수 API 연결 전 단계입니다.");
  }

  function handleAutoTrade() {
    const next = !autoTradeEnabled;
    const confirmed = window.confirm(next ? "자동매매를 켤까요?" : "자동매매를 끌까요?");
    if (!confirmed) return;
    setAutoTradeEnabled(next);
    setMessage(next ? "자동매매를 켰습니다." : "자동매매를 껐습니다.");
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

  const candleBars = useMemo(() => {
    if (candles.length === 0) return [];

    const visible = candles.slice(-24);
    const high = Math.max(...visible.map((c) => c.high));
    const low = Math.min(...visible.map((c) => c.low));
    const range = Math.max(1, high - low);

    return visible.map((candle) => ({
      key: `${candle.time}`,
      label: formatChartTime(candle.time),
      bodyBottom: ((Math.min(candle.open, candle.close) - low) / range) * 100,
      bodyHeight: Math.max((Math.abs(candle.close - candle.open) / range) * 100, 2),
      wickBottom: ((candle.low - low) / range) * 100,
      wickHeight: Math.max(((candle.high - candle.low) / range) * 100, 4),
      rising: candle.close >= candle.open
    }));
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
    <div className="dashboard-shell">
      {/* ── Topbar ── */}
      <header className="topbar">
        <div className="topbar-left">
          <div className="brand-chip">HQA</div>
          <div className="market-strip">
            {marketSnapshot.map((item) => (
              <div className="market-card" key={item.label}>
                <span className="market-label">{item.label}</span>
                <span className="market-value">{item.value}</span>
                <span className={`market-delta ${item.positive ? "positive" : "negative"}`}>{item.delta}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="topbar-actions">
          <button
            className={autoTradeEnabled ? "button-secondary" : "button-ghost"}
            onClick={handleAutoTrade}
            type="button"
            style={{ fontSize: "0.8rem" }}
          >
            자동매매 {autoTradeEnabled ? "ON" : "OFF"}
          </button>
          <Link className="button-ghost" href="/onboarding/preference" style={{ fontSize: "0.8rem" }}>
            마이페이지
          </Link>
          <button className="button-ghost" onClick={logout} type="button" style={{ fontSize: "0.8rem" }}>
            로그아웃
          </button>
        </div>
      </header>

      {/* ── Account Strip ── */}
      <div className="account-strip">
        <div className="account-card">
          <span className="account-label">현재 재산</span>
          <span className="account-value">{totalAssetsText}</span>
        </div>
        <div className="account-card">
          <span className="account-label">현재 수익률</span>
          <span className="account-value">-</span>
        </div>
        <div className="account-card">
          <span className="account-label">월 투자 금액</span>
          <span className="account-value">{monthlyInvestmentText}</span>
        </div>
      </div>

      {/* ── Main: Sidebar + Content ── */}
      <div className="dashboard-main">
        {/* Sidebar */}
        <aside className="sidebar">
          {/* Search */}
          <div className="sidebar-section">
            <form className="search-form" onSubmit={onSearch}>
              <input
                className="search-input"
                placeholder="종목 검색..."
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
              />
              <button className="button" disabled={searching} type="submit" style={{ flexShrink: 0 }}>
                {searching ? "..." : "검색"}
              </button>
            </form>
          </div>

          {/* Chart */}
          <div className="sidebar-section">
            <div className="chart-header">
              <div>
                <p className="chart-stock-name">{selected?.name ?? "종목 없음"}</p>
                <p className="chart-stock-code">{selected?.code ?? "-"}</p>
              </div>
              <div className="timeframe-switch">
                <button
                  className={timeframe === "1m" ? "tab-chip active" : "tab-chip"}
                  onClick={() => setTimeframe("1m")}
                  type="button"
                >
                  1분
                </button>
                <button
                  className={timeframe === "10m" ? "tab-chip active" : "tab-chip"}
                  onClick={() => setTimeframe("10m")}
                  type="button"
                >
                  10분
                </button>
              </div>
            </div>

            <div className="price-strip">
              <span className="price-main">{formatPrice(price?.currentPrice)}</span>
              <span className={`price-delta ${pricePositive ? "positive" : "negative"}`}>
                {formatSignedNumber(price?.change)}원
              </span>
              <span className="price-rate">{formatSignedRate(price?.changeRate)}</span>
            </div>

            <div className="chart-canvas chart-candles">
              {loadingChart ? <div className="chart-empty">로딩 중...</div> : null}
              {!loadingChart && chartError ? <div className="chart-empty">{chartError}</div> : null}
              {!loadingChart && !chartError && candleBars.length === 0 ? (
                <div className="chart-empty">차트 데이터 없음</div>
              ) : null}
              {!loadingChart && !chartError && candleBars.length > 0 ? (
                <div className="candle-bars">
                  {candleBars.map((bar) => (
                    <div className="candle-bar" key={bar.key} title={bar.label}>
                      <span
                        className={bar.rising ? "candle-wick rising" : "candle-wick falling"}
                        style={{ bottom: `${bar.wickBottom}%`, height: `${bar.wickHeight}%` }}
                      />
                      <span
                        className={bar.rising ? "candle-body rising" : "candle-body falling"}
                        style={{ bottom: `${bar.bodyBottom}%`, height: `${bar.bodyHeight}%` }}
                      />
                    </div>
                  ))}
                </div>
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
                <span className="quote-label">종가</span>
                <span className="quote-value">{formatPrice(candleStats?.close ?? price?.currentPrice)}</span>
              </div>
              <div className="quote-card">
                <span className="quote-label">거래량</span>
                <span className="quote-value mono">{formatNumber(candleStats?.volume ?? price?.volume)}</span>
              </div>
              <div className="quote-card">
                <span className="quote-label">갱신</span>
                <span className="quote-value mono">{candleStats ? formatChartTime(candleStats.time) : "-"}</span>
              </div>
            </div>

            <div className="candle-table" style={{ marginTop: 12 }}>
              <div className="candle-table-head">
                <span>시간</span>
                <span>시가</span>
                <span>고가</span>
                <span>저가</span>
                <span>종가</span>
                <span>거래량</span>
              </div>
              <div className="candle-table-body">
                {candles.slice(-8).reverse().map((candle) => (
                  <div className="candle-table-row" key={candle.time}>
                    <span className="mono">{formatChartTime(candle.time)}</span>
                    <span>{formatNumber(candle.open)}</span>
                    <span>{formatNumber(candle.high)}</span>
                    <span>{formatNumber(candle.low)}</span>
                    <span>{formatNumber(candle.close)}</span>
                    <span>{formatNumber(candle.volume)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Search Results */}
          <div className="sidebar-section" style={{ flex: 1, overflowY: "auto", borderBottom: "none" }}>
            {searchResults.length > 0 ? (
              <>
                <p style={{ fontSize: "0.72rem", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, margin: "0 0 8px" }}>
                  검색 결과 {searchResults.length}건
                </p>
                <div className="result-list">
                  {searchResults.map((item) => (
                    <button
                      key={`${item.code}-${item.market}`}
                      className={selected?.code === item.code ? "result-item active" : "result-item"}
                      onClick={() => setSelected(item)}
                      type="button"
                    >
                      <div>
                        <div className="result-name">{item.name}</div>
                        <div className="result-code">{item.code}</div>
                      </div>
                      <StatusPill
                        label={selected?.code === item.code ? "선택" : item.market}
                        tone={selected?.code === item.code ? "good" : "neutral"}
                      />
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <p style={{ fontSize: "0.8rem", color: "var(--muted)", margin: 0 }}>종목을 검색해주세요.</p>
            )}
          </div>
        </aside>

        {/* Main Content */}
        <main className="main-content">
          <div className="content-area">
            {/* Content Head */}
            <div className="content-head">
              <div>
                <h1 className="content-stock-name">
                  {selected?.name ?? "종목을 선택해주세요"}
                </h1>
                <p className="content-stock-sub mono">
                  {selected?.code ?? "-"} / {selected?.market ?? "-"}
                </p>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                {task ? <StatusPill label={task.status} tone="warn" /> : null}
                <div className="tab-switch">
                  <button
                    className={tab === "analysis" ? "tab-chip active" : "tab-chip"}
                    onClick={() => setTab("analysis")}
                    type="button"
                  >
                    분석
                  </button>
                  <button
                    className={tab === "buy" ? "tab-chip active" : "tab-chip"}
                    onClick={() => setTab("buy")}
                    type="button"
                  >
                    매수
                  </button>
                </div>
              </div>
            </div>

            {/* Tab Content */}
            {tab === "analysis" ? (
              <div style={{ display: "grid", gap: 16 }}>
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

                <div className="hero-board">
                  <div className="hero-copy">
                    <span className="hero-copy-label">분석 요청</span>
                    <span className="hero-copy-text">
                      {selected ? `${selected.name} 분석을 시작합니다.` : "종목을 선택해주세요."}
                    </span>
                  </div>
                  <button
                    className="button"
                    disabled={!selected || submitting}
                    onClick={submitAnalysis}
                    type="button"
                  >
                    {submitting ? "요청 중..." : "분석 시작"}
                  </button>
                </div>

                <div className="summary-card">
                  <div className="summary-row">
                    <span className="summary-label">최근 작업 ID</span>
                    <span className="summary-value mono">{task?.taskId ?? "-"}</span>
                  </div>
                  <div className="summary-row">
                    <span className="summary-label">자동매매</span>
                    <StatusPill
                      label={autoTradeEnabled ? "활성" : "비활성"}
                      tone={autoTradeEnabled ? "good" : "neutral"}
                    />
                  </div>
                  {user ? (
                    <div className="summary-row">
                      <span className="summary-label">사용자</span>
                      <span className="summary-value">{user.userId}</span>
                    </div>
                  ) : null}
                </div>
              </div>
            ) : (
              <div style={{ display: "grid", gap: 16 }}>
                <div className="buy-grid">
                  <div className="field">
                    <label htmlFor="buyQuantity">수량</label>
                    <input
                      id="buyQuantity"
                      min="1"
                      type="number"
                      value={buyQuantity}
                      onChange={(event) => setBuyQuantity(event.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="buyPrice">가격 (원)</label>
                    <input
                      id="buyPrice"
                      placeholder="시장가"
                      value={buyPrice}
                      onChange={(event) => setBuyPrice(event.target.value)}
                    />
                  </div>
                </div>

                <div className="hero-board">
                  <div className="hero-copy">
                    <span className="hero-copy-label">매수 주문</span>
                    <span className="hero-copy-text">
                      {selected ? `${selected.name} 주문 준비` : "종목을 선택해주세요."}
                    </span>
                  </div>
                  <button
                    className="button"
                    disabled={!selected}
                    onClick={handleBuy}
                    type="button"
                  >
                    매수하기
                  </button>
                </div>
              </div>
            )}

            {message ? <p className="message-line">{message}</p> : null}
          </div>
        </main>
      </div>
    </div>
  );
}
