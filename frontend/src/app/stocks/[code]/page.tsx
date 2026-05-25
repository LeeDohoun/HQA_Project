"use client";

/* ============================================================
   종목 상세 — /stocks/[code]
   차트 · 시세 · 주문 + 뉴스/공시/리포트 타임라인
   디자인은 /dashboard 의 에디토리얼 다크 톤을 그대로 따름.
   ============================================================ */

import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { authApi, chartApi, stockApi, tradingApi } from "@/lib/api";
import { TradingViewChart } from "@/components/common/tradingview-chart";
import type {
  AuthUser,
  Candle,
  DisclosureItem,
  NewsItem,
  RealtimePrice,
  StockSearchResult
} from "@/types/api";

/* ============================================================
   디자인 시스템 — /dashboard 와 동일 (에디토리얼 · 다크)
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

.ed-btn{
  display:inline-flex; align-items:center; justify-content:center; gap:8px; cursor:pointer;
  font-family:var(--sans); font-size:.94rem; font-weight:800; letter-spacing:-.01em;
  padding:12px 20px; border:1px solid transparent; border-radius:5px; white-space:nowrap;
  transition:transform .14s var(--ease),background .15s,opacity .15s;
}
.ed-btn:active{transform:translateY(1px);}
.ed-btn:disabled{opacity:.4; cursor:not-allowed;}
.ed-btn--moss{background:var(--moss); color:#0b2417;}
.ed-btn--ink{background:var(--ink); color:var(--paper);}
.ed-btn--line{background:transparent; color:var(--ink); border-color:var(--rule);}
.ed-btn--line:hover:not(:disabled){border-color:var(--ink-2);}
.ed-btn--buy{background:var(--up); color:#fff;}
.ed-btn--sell{background:var(--down); color:#fff;}
.ed-btn--sm{padding:9px 14px; font-size:.86rem;}
.ed-tlink{background:none; border:none; cursor:pointer; padding:0; font:inherit; color:var(--ink-2); font-weight:800; text-decoration:underline; text-underline-offset:4px;}
.ed-tlink:hover{color:var(--ink);}

.ed-nav{position:sticky; top:0; z-index:40; background:rgba(20,19,13,.92); backdrop-filter:saturate(150%) blur(12px); -webkit-backdrop-filter:saturate(150%) blur(12px); border-bottom:1px solid var(--rule);}
.ed-nav-in{max-width:1180px; margin:0 auto; padding:0 clamp(18px,4vw,52px); height:64px; display:flex; align-items:center; gap:10px;}
.ed-mark{display:inline-flex; align-items:baseline;}
.ed-mark b{font-family:var(--serif); font-style:italic; font-weight:700; font-size:1.42rem; letter-spacing:-.02em;}
.ed-mark i{width:6px; height:6px; border-radius:50%; background:var(--moss); margin-left:3px; align-self:flex-end; margin-bottom:5px;}
.ed-nav-right{margin-left:auto; display:flex; align-items:center; gap:10px;}

.ed-app{padding:clamp(24px,4vw,44px) 0 110px;}
.ed-app-head{margin-bottom:20px;}
.ed-kicker{font-family:var(--serif); font-style:italic; font-size:1.1rem; color:var(--ink-3);}
.ed-app-h{font-size:clamp(1.5rem,3vw,2.2rem); font-weight:800; letter-spacing:-.03em; margin:3px 0 0;}

.ed-sec{margin-top:34px;}
.ed-sec-head{display:flex; align-items:baseline; justify-content:space-between; gap:12px; padding-bottom:11px; border-bottom:1.5px solid var(--ink); margin-bottom:2px;}
.ed-sec-title{font-size:1.1rem; font-weight:800; letter-spacing:-.02em;}
.ed-sec-meta{font-size:.8rem; color:var(--ink-3); font-weight:700;}

.ed-pricebar{display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin:8px 0 0;}
.ed-price-now{font-family:var(--serif); font-size:2.4rem; font-weight:700;}
.ed-price-d{font-size:1.05rem; font-weight:800; font-variant-numeric:tabular-nums;}
.ed-chart-frame{height:380px; border:1px solid var(--rule); background:var(--card); padding:8px; margin-top:14px;}
.ed-chart-empty{height:100%; display:flex; align-items:center; justify-content:center; color:var(--ink-3); font-size:.9rem;}
.ed-quotegrid{display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--rule); border:1px solid var(--rule); margin-top:14px;}
.ed-quote-cell{background:var(--card); padding:11px 13px;}
.ed-quote-cell small{display:block; font-size:.72rem; color:var(--ink-3); font-weight:700;}
.ed-quote-cell b{font-family:var(--serif); font-size:1.02rem; font-weight:700;}

.ed-field{display:flex; flex-direction:column; gap:7px;}
.ed-flabel{font-size:.78rem; font-weight:800; color:var(--ink-2); letter-spacing:.02em;}
.ed-input{
  width:100%; background:var(--card); border:1px solid var(--rule); color:var(--ink);
  font-family:var(--sans); font-size:.95rem; padding:11px 13px; border-radius:5px; outline:none;
  transition:border-color .14s;
}
.ed-input:focus{border-color:var(--moss);}
.ed-seg{display:inline-flex; border:1px solid var(--rule); border-radius:5px; overflow:hidden;}
.ed-seg-btn{
  background:none; border:0; cursor:pointer; font:inherit; font-weight:800; font-size:.86rem;
  color:var(--ink-3); padding:9px 16px;
}
.ed-seg-btn + .ed-seg-btn{border-left:1px solid var(--rule);}
.ed-seg-btn--on{background:var(--ink); color:var(--paper);}
.ed-seg-btn--buy.ed-seg-btn--on{background:var(--up); color:#fff;}
.ed-seg-btn--sell.ed-seg-btn--on{background:var(--down); color:#fff;}

.ed-tablist{display:flex; gap:0; border-bottom:1px solid var(--rule); margin-top:14px;}
.ed-tab{
  background:none; border:none; cursor:pointer; font-family:var(--sans);
  font-size:.94rem; font-weight:800; color:var(--ink-3); padding:11px 4px; margin-right:24px;
  border-bottom:2px solid transparent; transition:color .14s,border-color .14s;
}
.ed-tab--on{color:var(--ink); border-bottom-color:var(--moss);}

.ed-timeline{display:flex; flex-direction:column;}
.ed-tl-row{
  display:grid; grid-template-columns:90px 1fr; gap:18px; align-items:start;
  padding:16px 4px; border-bottom:1px solid var(--rule);
}
.ed-tl-row:last-child{border-bottom:0;}
.ed-tl-date{font-family:var(--serif); font-size:.86rem; color:var(--ink-3); font-weight:700; line-height:1.3; padding-top:2px;}
.ed-tl-main{min-width:0;}
.ed-tl-title{
  font-weight:800; font-size:1rem; color:var(--ink);
  display:block; line-height:1.4;
  word-break:break-word;
}
.ed-tl-title:hover{color:var(--moss);}
.ed-tl-sum{margin-top:6px; font-size:.88rem; color:var(--ink-2); line-height:1.55;}
.ed-tl-meta{margin-top:6px; font-size:.78rem; color:var(--ink-3); font-weight:600;}
.ed-tl-tag{
  display:inline-flex; font-size:.7rem; font-weight:800; padding:2px 8px; border-radius:3px;
  margin-right:8px; vertical-align:middle;
  background:var(--rule); color:var(--ink-2);
}
.ed-tl-tag--dart{background:rgba(54,176,121,.18); color:var(--moss);}
.ed-tl-tag--news{background:rgba(224,163,65,.2); color:var(--spark);}

.ed-fade{animation:ed-fade .4s var(--ease) both;}
@keyframes ed-fade{from{opacity:0; transform:translateY(10px);}to{opacity:1; transform:translateY(0);}}

@media (max-width:680px){
  .ed-quotegrid{grid-template-columns:repeat(2,1fr);}
  .ed-tl-row{grid-template-columns:1fr; gap:6px;}
}
@media (prefers-reduced-motion:reduce){
  .ed *{animation-duration:.001ms !important; transition-duration:.001ms !important;}
}
`;

type OrderSide = "buy" | "sell";
type ChartTimeframe = "1d" | "1w" | "1M";
type FeedTab = "news" | "disclosures";

const TIMEFRAME_TABS: Array<{ value: ChartTimeframe; label: string }> = [
  { value: "1d", label: "일봉" },
  { value: "1w", label: "주봉" },
  { value: "1M", label: "월봉" }
];
const TIMEFRAME_COUNT: Record<ChartTimeframe, number> = {
  "1d": 200,
  "1w": 120,
  "1M": 60
};
const FEED_TABS: Array<{ value: FeedTab; label: string }> = [
  { value: "news", label: "뉴스" },
  { value: "disclosures", label: "공시" }
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
function formatDate(value: string | null | undefined) {
  if (!value) return "-";
  // 다양한 포맷이 들어옴: ISO, "20240115", "2024-01-15", 한글 등.
  // 파싱 실패시 원문 그대로.
  const isoLike = /^\d{4}-\d{2}-\d{2}/.test(value);
  const yyyymmdd = /^\d{8}$/.test(value);
  try {
    if (isoLike) {
      const d = new Date(value);
      if (!isNaN(d.getTime())) return d.toLocaleDateString("ko-KR", { year: "numeric", month: "short", day: "numeric" });
    } else if (yyyymmdd) {
      const y = value.slice(0, 4), m = value.slice(4, 6), d = value.slice(6, 8);
      return `${y}.${m}.${d}`;
    }
  } catch { /* ignore */ }
  return value;
}

/* ============================================================
   페이지
   ============================================================ */
export default function StockDetailPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const router = useRouter();

  const [user, setUser] = useState<AuthUser | null>(null);
  const [loadingUser, setLoadingUser] = useState(true);

  // 종목 메타 (이름 등) — 검색으로 보강
  const [stockMeta, setStockMeta] = useState<StockSearchResult | null>(null);

  // 시세/차트
  const [timeframe, setTimeframe] = useState<ChartTimeframe>("1d");
  const [price, setPrice] = useState<RealtimePrice | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loadingChart, setLoadingChart] = useState(false);
  const [chartError, setChartError] = useState("");
  const [hasMoreCandles, setHasMoreCandles] = useState(false);
  const loadingMoreRef = useRef(false);
  const seriesTokenRef = useRef(0);

  // 주문
  const [orderSide, setOrderSide] = useState<OrderSide>("buy");
  const [buyQuantity, setBuyQuantity] = useState("1");
  const [buyPrice, setBuyPrice] = useState("");
  const [message, setMessage] = useState("");

  // 피드 (뉴스/공시/리포트)
  const [feedTab, setFeedTab] = useState<FeedTab>("news");
  const [news, setNews] = useState<NewsItem[]>([]);
  const [disclosures, setDisclosures] = useState<DisclosureItem[]>([]);
  const [feedLoading, setFeedLoading] = useState(false);
  const [feedError, setFeedError] = useState("");

  // 사용자 인증 — 미로그인이면 로그인으로 보냄
  useEffect(() => {
    let active = true;
    authApi.me()
      .then((u) => { if (active) setUser(u); })
      .catch(() => { router.replace("/login"); })
      .finally(() => { if (active) setLoadingUser(false); });
    return () => { active = false; };
  }, [router]);

  // 종목 이름 — 검색 API로 한 번 조회. 실패해도 코드만 사용.
  useEffect(() => {
    let active = true;
    stockApi.search(code)
      .then((res) => {
        if (!active) return;
        const exact = res.results.find((r) => r.code === code) ?? res.results[0] ?? null;
        if (exact) setStockMeta(exact);
      })
      .catch(() => { /* 메타 없어도 페이지는 동작 */ });
    return () => { active = false; };
  }, [code]);

  // 시세 + 차트
  useEffect(() => {
    let active = true;
    const myToken = ++seriesTokenRef.current;
    loadingMoreRef.current = false;
    setHasMoreCandles(false);

    async function load() {
      setLoadingChart(true);
      setChartError("");
      try {
        const [priceResponse, candleResponse] = await Promise.all([
          stockApi.price(code),
          chartApi.history(code, timeframe, TIMEFRAME_COUNT[timeframe])
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
    void load();
    return () => { active = false; };
  }, [code, timeframe]);

  const loadMoreCandles = useCallback(async () => {
    if (loadingMoreRef.current) return;
    if (!hasMoreCandles) return;
    if (candles.length === 0) return;
    const myToken = seriesTokenRef.current;
    const before = candles[0].time;
    loadingMoreRef.current = true;
    try {
      const response = await chartApi.history(code, timeframe, TIMEFRAME_COUNT[timeframe], before);
      if (seriesTokenRef.current !== myToken) return;
      if (response.candles.length === 0) { setHasMoreCandles(false); return; }
      setCandles((prev) => {
        const oldestExisting = prev[0]?.time ?? Number.POSITIVE_INFINITY;
        const merged = [
          ...response.candles.filter((c) => c.time < oldestExisting),
          ...prev
        ];
        return merged;
      });
      setHasMoreCandles(response.hasMore);
    } catch {
      /* prepend 실패는 무시 */
    } finally {
      if (seriesTokenRef.current === myToken) loadingMoreRef.current = false;
    }
  }, [code, timeframe, hasMoreCandles, candles]);

  // 피드 로드 (탭 전환 시)
  useEffect(() => {
    let active = true;
    setFeedLoading(true);
    setFeedError("");
    const loader =
      feedTab === "news" ? stockApi.news(code)
      : stockApi.disclosures(code);

    loader
      .then((res) => {
        if (!active) return;
        if (res.error) setFeedError(res.error);
        if (feedTab === "news") setNews((res.items ?? []) as NewsItem[]);
        else setDisclosures((res.items ?? []) as DisclosureItem[]);
      })
      .catch((e) => {
        if (!active) return;
        setFeedError(e instanceof Error ? e.message : "자료를 불러오지 못했습니다.");
      })
      .finally(() => { if (active) setFeedLoading(false); });

    return () => { active = false; };
  }, [code, feedTab]);

  async function handleOrder() {
    const name = stockMeta?.name ?? code;
    const qty = Math.max(1, parseInt(buyQuantity || "1", 10) || 1);
    const orderPrice = Math.max(0, parseInt(buyPrice || "0", 10) || 0);
    const priceLabel = orderPrice > 0 ? `${orderPrice.toLocaleString("ko-KR")}원` : "시장가";
    const sideLabel = orderSide === "buy" ? "매수" : "매도";
    const confirmed = window.confirm(`${name} ${qty}주를 ${priceLabel}로 ${sideLabel}할까요?`);
    if (!confirmed) return;
    try {
      const payload = {
        stockName: name,
        stockCode: code,
        quantity: qty,
        limitPrice: orderPrice
      };
      const result = orderSide === "buy" ? await tradingApi.buy(payload) : await tradingApi.sell(payload);
      if (result.success) {
        setMessage(`${name} ${qty}주 ${sideLabel} 주문이 접수되었습니다.`);
      } else {
        const reason = result.error
          ?? (typeof result.response?.msg1 === "string" ? (result.response.msg1 as string) : `${sideLabel} 주문이 거부되었습니다.`);
        setMessage(`${sideLabel} 실패: ${reason}`);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `${sideLabel} 요청에 실패했습니다.`);
    }
  }

  const candleStats = useMemo(() => candles.length === 0 ? null : candles[candles.length - 1], [candles]);
  const priceChange = price?.change ?? 0;
  const pricePositive = priceChange >= 0;

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

  const stockName = stockMeta?.name ?? code;
  const market = stockMeta?.market;

  return (
    <div className="ed">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />

      <nav className="ed-nav">
        <div className="ed-nav-in">
          <span className="ed-mark" aria-label="HQA">
            <b>HQA</b><i />
          </span>
          <button type="button" className="ed-tlink" style={{ marginLeft: 16, fontSize: ".9rem" }} onClick={() => router.push("/dashboard")}>
            ← 대시보드
          </button>
          <div className="ed-nav-right">
            {user ? <span className="ed-fine">{user.firstName}님</span> : null}
          </div>
        </div>
      </nav>

      <main className="ed-app">
        <div className="ed-wrap ed-fade">
          <div className="ed-app-head">
            <div className="ed-kicker">종목</div>
            <h1 className="ed-app-h">{stockName}</h1>
            <p className="ed-fine" style={{ marginTop: 6 }}>
              {code}{market ? ` · ${market}` : ""}
            </p>
          </div>

          {/* 시세 */}
          <section className="ed-sec" style={{ marginTop: 14 }}>
            <div className="ed-pricebar">
              <span className="ed-price-now">{formatPrice(price?.currentPrice)}</span>
              <span className={`ed-price-d ${pricePositive ? "ed-up" : "ed-down"}`}>
                {formatSignedNumber(price?.change)}원
              </span>
              <span className={`ed-price-d ${pricePositive ? "ed-up" : "ed-down"}`}>
                {formatSignedRate(price?.changeRate)}
              </span>
            </div>

            <div className="ed-seg" style={{ marginTop: 14 }}>
              {TIMEFRAME_TABS.map((tf) => (
                <button
                  key={tf.value}
                  type="button"
                  className={`ed-seg-btn${timeframe === tf.value ? " ed-seg-btn--on" : ""}`}
                  onClick={() => setTimeframe(tf.value)}
                >
                  {tf.label}
                </button>
              ))}
            </div>

            <div className="ed-chart-frame">
              {loadingChart ? <div className="ed-chart-empty">로딩 중...</div> : null}
              {!loadingChart && chartError ? <div className="ed-chart-empty">{chartError}</div> : null}
              {!loadingChart && !chartError && candles.length === 0 ? (
                <div className="ed-chart-empty">차트 데이터 없음</div>
              ) : null}
              {!loadingChart && !chartError && candles.length > 0 ? (
                <TradingViewChart
                  candles={candles}
                  timeframe={timeframe}
                  onScrolledPastHalfLeft={loadMoreCandles}
                />
              ) : null}
            </div>

            <div className="ed-quotegrid">
              <div className="ed-quote-cell">
                <small>시가</small>
                <b>{formatPrice(candleStats?.open ?? price?.openPrice)}</b>
              </div>
              <div className="ed-quote-cell">
                <small>고가</small>
                <b>{formatPrice(candleStats?.high ?? price?.highPrice)}</b>
              </div>
              <div className="ed-quote-cell">
                <small>저가</small>
                <b>{formatPrice(candleStats?.low ?? price?.lowPrice)}</b>
              </div>
              <div className="ed-quote-cell">
                <small>거래량</small>
                <b>{formatNumber(candleStats?.volume ?? price?.volume)}</b>
              </div>
            </div>
          </section>

          {/* 주문 */}
          <section className="ed-sec">
            <div className="ed-sec-head">
              <span className="ed-sec-title">매수 · 매도 주문</span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-end", marginTop: 16 }}>
              <div className="ed-seg">
                <button
                  type="button"
                  className={`ed-seg-btn ed-seg-btn--buy${orderSide === "buy" ? " ed-seg-btn--on" : ""}`}
                  onClick={() => setOrderSide("buy")}
                >매수</button>
                <button
                  type="button"
                  className={`ed-seg-btn ed-seg-btn--sell${orderSide === "sell" ? " ed-seg-btn--on" : ""}`}
                  onClick={() => setOrderSide("sell")}
                >매도</button>
              </div>
              <div className="ed-field" style={{ width: 120 }}>
                <label className="ed-flabel" htmlFor="ed-qty">수량 (주)</label>
                <input
                  id="ed-qty"
                  className="ed-input"
                  type="number"
                  min="1"
                  value={buyQuantity}
                  onChange={(e) => setBuyQuantity(e.target.value)}
                />
              </div>
              <div className="ed-field" style={{ width: 180 }}>
                <label className="ed-flabel" htmlFor="ed-price">지정가 (원)</label>
                <input
                  id="ed-price"
                  className="ed-input"
                  inputMode="numeric"
                  placeholder="비워두면 시장가"
                  value={buyPrice}
                  onChange={(e) => setBuyPrice(e.target.value)}
                />
              </div>
              <button
                type="button"
                className={`ed-btn ${orderSide === "buy" ? "ed-btn--buy" : "ed-btn--sell"}`}
                onClick={handleOrder}
              >
                {orderSide === "buy" ? "매수 주문" : "매도 주문"}
              </button>
            </div>
            {message ? (
              <p className="ed-hint" style={{ marginTop: 14, color: "var(--ink-2)", borderLeft: "3px solid var(--moss)", paddingLeft: 12 }}>
                {message}
              </p>
            ) : null}
          </section>

          {/* 자료 (뉴스 · 공시 · 리포트) */}
          <section className="ed-sec">
            <div className="ed-sec-head">
              <span className="ed-sec-title">종목 자료</span>
              <span className="ed-sec-meta">최근 수집된 자료</span>
            </div>
            <div className="ed-tablist">
              {FEED_TABS.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  className={`ed-tab${feedTab === t.value ? " ed-tab--on" : ""}`}
                  onClick={() => setFeedTab(t.value)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {feedLoading ? (
              <p className="ed-hint" style={{ padding: "20px 4px" }}>불러오는 중...</p>
            ) : feedError ? (
              <p className="ed-hint" style={{ padding: "20px 4px", color: "var(--up)" }}>
                {feedError}
              </p>
            ) : (
              <>
                {feedTab === "news" && (
                  <FeedList
                    empty="수집된 뉴스가 아직 없어요."
                    items={news.map((n, i) => ({
                      key: `n-${n.url || i}`,
                      date: n.publishedAt ?? n.createdAt,
                      title: n.title,
                      url: n.url,
                      summary: n.summary,
                      meta: n.source ?? "",
                      tag: "뉴스",
                      tagKind: "news" as const
                    }))}
                  />
                )}
                {feedTab === "disclosures" && (
                  <FeedList
                    empty="수집된 공시가 아직 없어요."
                    items={disclosures.map((d, i) => ({
                      key: `d-${d.receiptNo || d.url || i}`,
                      date: d.receiptDate ?? d.createdAt,
                      title: d.reportName,
                      url: d.url,
                      summary: null,
                      meta: d.submitter ?? "",
                      tag: "DART",
                      tagKind: "dart" as const
                    }))}
                  />
                )}
              </>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}

/* ============================================================
   피드 리스트 — 뉴스/공시/리포트 공통 렌더러
   ============================================================ */
type FeedRow = {
  key: string;
  date: string | null;
  title: string;
  url: string;
  summary: string | null;
  meta: string;
  tag: string;
  tagKind: "news" | "dart";
};

function FeedList({ items, empty }: { items: FeedRow[]; empty: string }) {
  if (items.length === 0) {
    return <p className="ed-hint" style={{ padding: "20px 4px" }}>{empty}</p>;
  }
  return (
    <div className="ed-timeline">
      {items.map((it) => (
        <div className="ed-tl-row" key={it.key}>
          <div className="ed-tl-date">{formatDate(it.date)}</div>
          <div className="ed-tl-main">
            <a
              className="ed-tl-title"
              href={it.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <span className={`ed-tl-tag ed-tl-tag--${it.tagKind}`}>{it.tag}</span>
              {it.title}
            </a>
            {it.summary ? <p className="ed-tl-sum">{it.summary}</p> : null}
            {it.meta ? <p className="ed-tl-meta">{it.meta}</p> : null}
          </div>
        </div>
      ))}
    </div>
  );
}
