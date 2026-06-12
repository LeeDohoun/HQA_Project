"use client";

/* ============================================================
   대시보드 — 워치리스트 / AI 분석 / 거래 내역 / 내 자산 4탭
   디자인은 /prototype 의 에디토리얼 톤(다크). 기능·API 로직은 기존과 동일.
   ============================================================ */

import { useRouter } from "next/navigation";
import { Dispatch, FormEvent, SetStateAction, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentDetailSections, AnalysisSummaryCard } from "@/components/common/analysis-report";
import { analysisApi, authApi, eventStreamUrl, parseAgentResultEvent, parseProgressEvent, stockApi, tradingApi, watchlistApi } from "@/lib/api";
import type {
  AnalysisAgentResultEvent,
  AnalysisHistoryItem,
  AnalysisMode,
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
.ed-statuschip:disabled{opacity:.55; cursor:not-allowed;}
.ed-statuschip--on{border-color:var(--moss); color:var(--moss);}
.ed-navbal{
  display:inline-flex; flex-direction:column; align-items:flex-end; justify-content:center;
  height:36px; padding:0 13px; border:1px solid var(--rule); border-radius:5px;
  background:transparent; cursor:pointer; line-height:1.05; text-align:right;
}
.ed-navbal:hover{border-color:var(--ink-3);}
.ed-navbal small{font-size:.62rem; font-weight:800; letter-spacing:.04em; color:var(--ink-3);}
.ed-navbal b{font-size:.86rem; font-weight:800; color:var(--ink); font-family:var(--serif);}
.ed-navbal--loading b{color:var(--ink-3);}
@media (max-width:560px){ .ed-navbal small{display:none;} }

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

/* 확인 모달 */
.ed-modal-backdrop{
  position:fixed; inset:0; z-index:80; display:flex; align-items:center; justify-content:center;
  padding:20px; background:rgba(7,7,5,.68); backdrop-filter:blur(10px);
}
.ed-modal{
  width:min(480px,100%); border:1px solid rgba(236,230,211,.16); background:linear-gradient(180deg,#211f15,#17160f);
  box-shadow:0 24px 80px rgba(0,0,0,.42); padding:20px;
}
.ed-modal-kicker{display:flex; align-items:center; gap:8px; color:var(--moss); font-size:.78rem; font-weight:900; letter-spacing:.08em;}
.ed-modal-title{margin:12px 0 0; font-size:1.28rem; line-height:1.3; font-weight:900; letter-spacing:-.02em;}
.ed-modal-copy{margin:9px 0 0; color:var(--ink-2); font-size:.92rem; line-height:1.65;}
.ed-modal-list{display:grid; gap:7px; margin:15px 0 0; padding:0; list-style:none;}
.ed-modal-list li{display:flex; align-items:flex-start; gap:9px; color:var(--ink-2); font-size:.84rem;}
.ed-modal-list li::before{content:""; width:6px; height:6px; margin-top:8px; border-radius:50%; background:var(--spark); flex:0 0 auto;}
.ed-modal-actions{display:flex; justify-content:flex-end; gap:8px; margin-top:20px; flex-wrap:wrap;}

/* AI 분석 콘솔 */
.ed-console-hero{
  display:grid; grid-template-columns:minmax(0,1.45fr) minmax(280px,.75fr); gap:14px;
  margin-top:18px; align-items:stretch;
}
.ed-console-card{
  border:1px solid var(--rule); background:linear-gradient(180deg,rgba(31,28,18,.96),rgba(26,24,16,.96));
  padding:18px; min-width:0;
}
.ed-console-card--primary{border-color:rgba(54,176,121,.22); box-shadow:inset 0 1px 0 rgba(236,230,211,.04);}
.ed-console-title{font-size:1.05rem; font-weight:800; letter-spacing:-.01em; margin:0;}
.ed-console-sub{margin:5px 0 0; color:var(--ink-3); font-size:.88rem; line-height:1.55;}
.ed-console-actions{display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin-top:16px;}
.ed-console-stats{display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; background:var(--rule); border:1px solid var(--rule); margin-top:16px;}
.ed-console-stat{background:rgba(20,19,13,.88); padding:12px;}
.ed-console-stat small{display:block; color:var(--ink-3); font-size:.7rem; font-weight:800; letter-spacing:.04em;}
.ed-console-stat b{display:block; margin-top:4px; font-family:var(--serif); font-size:1.15rem; font-weight:700;}
.ed-pillbar{display:flex; flex-wrap:wrap; gap:7px; margin-top:12px;}
.ed-pill{display:inline-flex; align-items:center; gap:7px; border:1px solid var(--rule); padding:6px 9px; color:var(--ink-2); font-size:.77rem; font-weight:800;}
.ed-pill--live{border-color:rgba(54,176,121,.38); color:var(--moss); background:rgba(54,176,121,.08);}
.ed-confirm-panel{
  margin-top:14px; border:1px solid rgba(224,163,65,.34); background:rgba(224,163,65,.08);
  padding:14px; display:grid; gap:12px;
}
.ed-confirm-title{font-weight:800; letter-spacing:-.01em;}
.ed-confirm-copy{margin:3px 0 0; color:var(--ink-2); font-size:.86rem; line-height:1.55;}
.ed-confirm-actions{display:flex; gap:8px; flex-wrap:wrap;}
.ed-console-grid{display:grid; grid-template-columns:minmax(280px,.9fr) minmax(0,1.45fr); gap:18px; margin-top:18px; align-items:start;}
.ed-console-col{min-width:0;}
.ed-console-panel{border:1px solid var(--rule); background:rgba(31,28,18,.62); padding:16px;}
.ed-panel-head{display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding-bottom:12px; border-bottom:1px solid var(--rule);}
.ed-panel-title{font-weight:800; letter-spacing:-.01em;}
.ed-panel-meta{font-size:.78rem; color:var(--ink-3); font-weight:800;}
.ed-watch-tools{display:flex; gap:8px; margin:12px 0; flex-wrap:wrap;}
.ed-stock-grid{display:grid; grid-template-columns:1fr; gap:7px; margin-top:10px; max-height:430px; overflow:auto; padding-right:3px;}
.ed-stock-pick{
  display:flex; align-items:center; gap:11px; width:100%; border:1px solid var(--rule); background:rgba(20,19,13,.62);
  color:inherit; font:inherit; padding:10px; text-align:left; cursor:pointer; transition:border-color .14s,background .14s;
}
.ed-stock-pick:hover{border-color:var(--ink-3); background:rgba(236,230,211,.035);}
.ed-stock-pick--on{border-color:rgba(54,176,121,.45); background:rgba(54,176,121,.08);}
.ed-check{
  width:20px; height:20px; flex:0 0 20px; display:inline-flex; align-items:center; justify-content:center;
  border:1px solid var(--rule); color:transparent; font-size:.78rem; font-weight:900;
}
.ed-stock-pick--on .ed-check{border-color:var(--moss); background:var(--moss); color:#0b2417;}
.ed-workbench{display:grid; gap:14px;}
.ed-live-head{display:flex; align-items:flex-start; justify-content:space-between; gap:14px;}
.ed-live-name{font-weight:800; font-size:1.05rem; margin:0;}
.ed-live-id{color:var(--ink-3); font-size:.78rem; font-weight:800; margin-top:3px; font-variant-numeric:tabular-nums;}
.ed-live-progress{margin-top:14px; display:grid; gap:8px;}
.ed-agent-timeline{display:grid; gap:8px; margin-top:12px;}
.ed-agent-step{
  display:grid; grid-template-columns:32px minmax(0,1fr) auto; gap:11px; align-items:center;
  border:1px solid var(--rule); background:rgba(20,19,13,.55); padding:10px;
}
.ed-agent-step-mark{
  width:32px; height:32px; display:inline-flex; align-items:center; justify-content:center;
  border:1px solid var(--rule); color:var(--ink-3); font-size:.68rem; font-weight:900;
}
.ed-agent-step--running{border-color:rgba(224,163,65,.35);}
.ed-agent-step--running .ed-agent-step-mark{border-color:rgba(224,163,65,.5); color:var(--spark); background:rgba(224,163,65,.08);}
.ed-agent-step--done{border-color:rgba(54,176,121,.35);}
.ed-agent-step--done .ed-agent-step-mark{border-color:rgba(54,176,121,.45); color:var(--moss); background:rgba(54,176,121,.08);}
.ed-agent-name{display:block; font-weight:800; font-size:.9rem;}
.ed-agent-msg{display:block; color:var(--ink-3); font-size:.78rem; line-height:1.45; margin-top:1px;}
.ed-task-list{display:grid; gap:7px; margin-top:12px; max-height:360px; overflow:auto; padding-right:3px;}
.ed-task-item{
  display:grid; grid-template-columns:34px minmax(0,1fr) auto; align-items:center; gap:10px; width:100%;
  border:1px solid var(--rule); background:rgba(20,19,13,.52); color:inherit; font:inherit; padding:10px; text-align:left; cursor:pointer;
}
.ed-task-item:hover{border-color:var(--ink-3);}
.ed-task-item--on{border-color:rgba(54,176,121,.45); background:rgba(54,176,121,.08);}
.ed-task-badge{width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--rule); font-size:.68rem; font-weight:900; color:var(--ink-2);}
.ed-empty-panel{border:1px dashed var(--rule); padding:18px; color:var(--ink-3); font-size:.88rem; line-height:1.6;}

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
  .ed-console-hero,.ed-console-grid{grid-template-columns:1fr;}
  .ed-console-stats{grid-template-columns:1fr;}
  .ed-live-head{display:grid;}
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
  const [watchlist, setWatchlist] = useState<StockSearchResult[]>([]);
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [selectedAnalysisCodes, setSelectedAnalysisCodes] = useState<string[]>([]);
  const [selected, setSelected] = useState<StockSearchResult | null>(null);
  const [mode, setMode] = useState<AnalysisMode>("full");
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
  const [analysisAgentEvents, setAnalysisAgentEvents] = useState<AnalysisAgentResultEvent[]>([]);
  const [analysisError, setAnalysisError] = useState("");
  const [bulkTasks, setBulkTasks] = useState<AnalysisTaskResponse[]>([]);
  const analysisStreamRef = useRef<EventSource | null>(null);
  const analysisProgressPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const analysisProgressCursorRef = useRef<{ taskId: string; count: number }>({ taskId: "", count: 0 });
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

        // KIS 계좌가 연결된 사용자는 로그인 직후 곧바로 실계좌 잔고를 불러와
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
    if (selected?.code !== stock.code) {
      closeAnalysisStream();
      setAnalysisResult(null);
      setAnalysisProgress(null);
      setAnalysisAgentEvents([]);
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

  const closeAnalysisStream = useCallback(() => {
    analysisStreamRef.current?.close();
    analysisStreamRef.current = null;
  }, []);

  const closeAnalysisProgressPoll = useCallback(() => {
    if (analysisProgressPollRef.current) {
      clearInterval(analysisProgressPollRef.current);
      analysisProgressPollRef.current = null;
    }
  }, []);

  const applyAnalysisProgressEvent = useCallback((type: string, data: Record<string, unknown>) => {
    try {
      if (type === "progress") {
        setAnalysisProgress(parseProgressEvent(JSON.stringify(data)));
        return;
      }
      if (type === "agent_result") {
        const next = parseAgentResultEvent(JSON.stringify(data));
        setAnalysisAgentEvents((prev) => {
          const withoutDuplicate = prev.filter((item) => item.agent !== next.agent);
          return [...withoutDuplicate, next];
        });
      }
    } catch {
      /* ignore malformed progress payloads */
    }
  }, []);

  const pollAnalysisProgress = useCallback(async (taskId: string) => {
    try {
      const snapshot = await analysisApi.progress(taskId);
      if (analysisProgressCursorRef.current.taskId !== taskId) {
        analysisProgressCursorRef.current = { taskId, count: 0 };
      }
      const cursor = analysisProgressCursorRef.current.count;
      snapshot.events.slice(cursor).forEach((event) => applyAnalysisProgressEvent(event.type, event.data));
      analysisProgressCursorRef.current.count = snapshot.events.length;

      if (snapshot.status === "completed" || snapshot.status === "failed") {
        try {
          setAnalysisResult(await analysisApi.result(taskId));
        } catch (e) {
          setAnalysisError(e instanceof Error ? e.message : "분석 결과를 불러오지 못했습니다.");
        } finally {
          closeAnalysisProgressPoll();
          closeAnalysisStream();
        }
      }
    } catch {
      /* polling is a fallback; keep trying while the task is active */
    }
  }, [applyAnalysisProgressEvent, closeAnalysisProgressPoll, closeAnalysisStream]);

  const startAnalysisStream = useCallback((taskId: string) => {
    closeAnalysisStream();
    closeAnalysisProgressPoll();
    analysisProgressCursorRef.current = { taskId, count: 0 };
    void pollAnalysisProgress(taskId);
    analysisProgressPollRef.current = setInterval(() => {
      void pollAnalysisProgress(taskId);
    }, 2500);

    const source = new EventSource(eventStreamUrl(`/api/v1/analysis/${taskId}/stream`), {
      withCredentials: true
    });
    analysisStreamRef.current = source;

    source.addEventListener("progress", (event) => {
      try {
        setAnalysisProgress(parseProgressEvent((event as MessageEvent<string>).data));
      } catch {
        /* ignore malformed progress payloads */
      }
    });

    source.addEventListener("agent_result", (event) => {
      try {
        const next = parseAgentResultEvent((event as MessageEvent<string>).data);
        setAnalysisAgentEvents((prev) => {
          const withoutDuplicate = prev.filter((item) => item.agent !== next.agent);
          return [...withoutDuplicate, next];
        });
      } catch {
        /* ignore malformed agent payloads */
      }
    });

    source.addEventListener("completed", async () => {
      try {
        const latest = await analysisApi.result(taskId);
        setAnalysisResult(latest);
      } catch (e) {
        setAnalysisError(e instanceof Error ? e.message : "분석 결과를 불러오지 못했습니다.");
      } finally {
        closeAnalysisProgressPoll();
        closeAnalysisStream();
      }
    });

    source.onerror = async () => {
      try {
        const latest = await analysisApi.result(taskId);
        setAnalysisResult(latest);
        if (latest.status === "completed" || latest.status === "failed") {
          closeAnalysisProgressPoll();
          closeAnalysisStream();
        }
      } catch {
        /* keep the connection open for retries unless the result is final */
      }
    };
  }, [closeAnalysisProgressPoll, closeAnalysisStream, pollAnalysisProgress]);

  useEffect(() => () => {
    closeAnalysisProgressPoll();
    closeAnalysisStream();
  }, [closeAnalysisProgressPoll, closeAnalysisStream]);

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
        mode,
        mode === "full" ? 1 : 0,
        selectedStocks.map((stock) => ({ stockName: stock.name, stockCode: stock.code }))
      );
      if (result.submitted === 0) {
        setMessage("분석할 종목이 없습니다. (워치리스트 비어 있음)");
        setBulkTasks([]);
      } else {
        const failedNote = result.failed > 0 ? ` (실패 ${result.failed}건)` : "";
        const firstTask = result.tasks[0] ?? null;
        setTask(firstTask);
        setAnalysisResult(null);
        setAnalysisProgress(null);
        setAnalysisAgentEvents([]);
        setAnalysisError("");
        setBulkTasks(result.tasks);
        if (firstTask) {
          startAnalysisStream(firstTask.taskId);
        }
        setMessage(`${result.submitted}개 종목 분석을 시작했습니다${failedNote}. 첫 번째 종목의 진행 로그를 바로 표시합니다.`);
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
    setAnalysisAgentEvents([]);
    setAnalysisError("");
    startAnalysisStream(nextTask.taskId);
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
              {autoTradeSaving ? "변경 중..." : `자동매매 ${autoTradeEnabled ? "ON" : "OFF"}`}
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
            preference={preference}
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
            mode={mode}
            setMode={setMode}
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
            agentEvents={analysisAgentEvents}
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
  mode: AnalysisMode;
  setMode: (m: AnalysisMode) => void;
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
  agentEvents: AnalysisAgentResultEvent[];
  error: string;
}) {
  const {
    watchlist, watchlistLoading, selectedAnalysisCodes, setSelectedAnalysisCodes,
    mode, setMode, bulkAnalyzing, bulkConfirmOpen,
    requestBulkAnalyze, confirmBulkAnalyze, cancelBulkAnalyze,
    autoTradeEnabled, task, bulkTasks, onTrackTask, result, progress, agentEvents, error
  } = props;
  const selectedSet = new Set(selectedAnalysisCodes);
  const selectedCount = watchlist.filter((stock) => selectedSet.has(stock.code)).length;
  const toggleAnalysisStock = (code: string) => {
    setSelectedAnalysisCodes((prev) =>
      prev.includes(code) ? prev.filter((item) => item !== code) : [...prev, code]
    );
  };
  const currentTaskLabel = task?.message.replace(/\s*analysis queued\s*$/i, "");
  const modeEstimate = mode === "full" ? "약 3-6분" : "약 1분";

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
              ? `${task.taskId.slice(0, 8)} 작업의 에이전트 진행상황을 실시간으로 표시합니다.`
              : selectedCount > 0
                ? `${selectedCount}개 종목이 선택됐습니다. ${mode === "full" ? "전체 분석" : "빠른 분석"}으로 실행할 수 있습니다.`
                : "워치리스트에서 분석할 종목을 체크한 뒤 선택 종목 실행을 누르세요."}
          </p>

          <div className="ed-console-actions">
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
                  {selectedCount}개 종목을 {mode === "full" ? "전체 분석" : "빠른 분석"}으로 실행합니다
                </div>
                <p className="ed-confirm-copy">
                  {mode === "full"
                    ? "전체 분석은 종목당 수 분이 걸릴 수 있고, 첫 번째 작업의 진행 로그가 즉시 표시됩니다."
                    : "빠른 분석은 Quant와 Chartist 중심으로 먼저 판단합니다."}
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
            <span className="ed-pill ed-pill--live">모드 {mode === "full" ? "전체" : "빠른"}</span>
            <span className="ed-pill">예상 {modeEstimate}</span>
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
            전체 분석은 Analyst, Quant, Chartist, Risk Manager를 순차적으로 확인하므로 완료까지 시간이 걸릴 수 있습니다.
          </p>
        </div>
      </div>

      {autoTradeEnabled ? (
        <p className="ed-msg" style={{ marginTop: 14 }}>
          자동매매가 켜져 있어 분석 작업은 GPU 큐에서 대기할 수 있습니다.
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
          {(task || result || progress || agentEvents.length > 0 || error) ? (
            <AnalysisPanel task={task} result={result} progress={progress} agentEvents={agentEvents} error={error} />
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
                분석을 실행하면 이 영역에 에이전트 진행 로그, 진행률, 최종 판단이 표시됩니다.
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
        항목을 선택하면 위 분석 결과 영역에서 해당 종목의 진행 로그와 결과를 확인할 수 있습니다.
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
                {active ? "LIVE" : "AI"}
              </span>
              <span className="ed-row-main">
                <span className="ed-row-name">{stockLabel}</span>
                <span className="ed-row-meta">
                  {item.taskId.slice(0, 8)} · 예상 {Math.ceil(item.estimatedTimeSeconds / 60)}분
                </span>
              </span>
              <span className={`ed-tag ed-tag--${active ? "good" : analysisStatusTone(item.status)}`}>
                {active ? "추적 중" : translateAnalysisStatus(item.status)}
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

function agentStepClass(status?: string | null) {
  if (status === "completed") return "ed-agent-step ed-agent-step--done";
  if (status === "failed" || status === "error") return "ed-agent-step ed-agent-step--running";
  return "ed-agent-step ed-agent-step--running";
}

function AnalysisPanel({
  task,
  result,
  progress,
  agentEvents,
  error
}: {
  task: AnalysisTaskResponse | null;
  result: AnalysisResult | null;
  progress: AnalysisProgressEvent | null;
  agentEvents: AnalysisAgentResultEvent[];
  error: string;
}) {
  const status = result?.status ?? task?.status ?? "pending";
  const isFinished = status === "completed" || status === "failed";
  const percent = progress ? Math.round(progress.progress * 100) : null;
  const taskLabel = task?.message.replace(/\s*analysis queued\s*$/i, "");
  const modeLabel = result?.mode === "quick" ? "빠른 분석" : result?.mode === "full" ? "전체 분석" : "진행 중";

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

      {agentEvents.length > 0 ? (
        <div style={{ marginTop: 18 }}>
          <p className="ed-label" style={{ marginBottom: 8 }}>에이전트 진행 로그</p>
          <div className="ed-agent-timeline">
            {agentEvents.map((event) => (
              <div className={agentStepClass(event.status)} key={event.agent}>
                <span className="ed-agent-step-mark">
                  {event.label.slice(0, 2).toUpperCase()}
                </span>
                <span>
                  <span className="ed-agent-name">{event.label}</span>
                  <span className="ed-agent-msg">{event.message}</span>
                  {event.opinion ? (
                    <span className="ed-agent-msg" style={{ color: "var(--ink-2)" }}>{event.opinion}</span>
                  ) : null}
                </span>
                <span className={`ed-tag ed-tag--${analysisStatusTone(event.status)}`}>
                  {typeof event.totalScore === "number" && event.totalScore > 0
                    ? `${Math.round(event.totalScore)}점`
                    : event.grade ?? translateAnalysisStatus(event.status)}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

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
  totalAssetsText,
  monthlyInvestmentText,
  onGoKis,
  onGoPreference
}: {
  preference: UserPreference | null;
  user: AuthUser | null;
  balance: Balance | null;
  totalAssetsText: string;
  monthlyInvestmentText: string;
  onGoKis: () => void;
  onGoPreference: () => void;
}) {
  // KIS 계좌가 연결되어 실잔고가 있으면 설문 입력값(totalAssets) 대신 실평가금액을
  // 계정 전체 잔고로 사용한다.
  const summary = balance?.summary;
  const kisLinked = !!user?.kisConfigured && summary?.totalEvalAmount != null;
  const totalAssetDisplay = kisLinked ? formatPrice(summary!.totalEvalAmount) : totalAssetsText;

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
          <small>{kisLinked ? "계정 전체 잔고 (KIS 실계좌)" : "보유 자산"}</small>
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
            ? "KIS 실계좌 잔고를 실시간으로 불러와 계정 전체 잔고로 표시하고 있어요."
            : "더 정확한 평가 자산은 KIS 계좌를 연결한 뒤 확인할 수 있어요."}
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
    user, preference, balance, balanceLoading, balanceError,
    ordersData, autoTradeEnabled, recentAnalyses, recentAnalysesLoading, aiActivity, aiActivityLoading,
    autoTradeExplanations, autoTradeExplanationsLoading, indices,
    onRefresh, onGoTab, onGoKis, onGoBacktest, onSelectStock
  } = props;

  const hasSnapshotBalance = balance?.source === "historical_runtime_snapshot" || balance?.source === "database_trade_signals";
  const kisConfigured = !!user?.kisConfigured || hasSnapshotBalance;
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
                      {leader.score}점 · 신뢰도 {leader.confidence}%
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
    </>
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
