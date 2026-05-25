"use client";

/* ============================================================
   HQA · AI 주식 자동매매 — 에디토리얼 웹 프로토타입 (단일 파일)
   편집 디자인 · 실제 인물 사진 · 백테스트 결과 · 완전 반응형
   외부 import 없음(react만) → 미리보기/Next.js 모두 동작
   화면: 랜딩(홈) → 온보딩 → 운용 · 전략 · 기록 · 설정
   ============================================================ */

import { useEffect, useState } from "react";
import type { ReactNode, SVGProps } from "react";

/* ============================================================
   1. 디자인 시스템 — 따뜻한 종이 · 세리프 · 풀블리드 밴드
   ============================================================ */
const CSS = `
.ed{
  --paper:#f2eee2; --paper-2:#e7e0cd; --ink:#191712; --ink-2:#57523f; --ink-3:#928b73;
  --card:#fbf8ef; --forest:#173d31; --forest-ink:#e9e4cf;
  --moss:#1f7a4f; --moss-2:#27935f;
  --spark:#cf8a2a;
  --rule:#d5cbae;
  --up:#bc3a2c; --down:#2f59b6;
  --serif:Georgia,"Times New Roman",serif;
  --sans:-apple-system,BlinkMacSystemFont,"Pretendard","Apple SD Gothic Neo","Noto Sans KR","Segoe UI",sans-serif;
  --lift:0 16px 38px rgba(28,20,8,.16);
  --ease:cubic-bezier(.22,1,.36,1);
  background:var(--paper); color:var(--ink); font-family:var(--sans);
  font-size:16px; line-height:1.6; -webkit-font-smoothing:antialiased; min-height:100vh;
}
.ed[data-theme="dark"]{
  --paper:#14130d; --paper-2:#1d1b12; --ink:#ece6d3; --ink-2:#a39c84; --ink-3:#6d6753;
  --card:#1f1c12; --forest:#1c5040; --forest-ink:#e9e4cf;
  --moss:#36b079; --moss-2:#43c489;
  --spark:#e0a341;
  --rule:#322d1f;
  --up:#d2554a; --down:#5d83d6;
  --lift:0 16px 38px rgba(0,0,0,.55);
}
.ed *{box-sizing:border-box;}
.ed img{display:block; max-width:100%;}
.ed-up{color:var(--up);} .ed-down{color:var(--down);}
.ed-serif{font-family:var(--serif);}
.ed-tnum{font-variant-numeric:tabular-nums;}

/* ---------- 공통 레이아웃 ---------- */
.ed-wrap{max-width:1180px; margin:0 auto; padding:0 clamp(20px,5vw,60px);}
.ed-band{padding:clamp(56px,9vw,108px) 0;}
.ed-band--forest{background:var(--forest); color:var(--forest-ink);}
.ed-band--paper2{background:var(--paper-2);}
.ed-rule{height:1px; background:var(--rule); border:0; margin:0;}
.ed-label{
  font-size:.74rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase;
  color:var(--moss);
}
.ed-band--forest .ed-label{color:var(--spark);}
.ed-eyebrow{
  display:inline-flex; align-items:center; gap:9px;
  font-size:.78rem; font-weight:800; letter-spacing:.13em; text-transform:uppercase; color:var(--ink-2);
}
.ed-dot{width:7px; height:7px; border-radius:50%; background:var(--moss);}
.ed-dot--live{background:var(--spark); animation:ed-pulse 2s infinite;}
@keyframes ed-pulse{0%{box-shadow:0 0 0 0 rgba(207,138,42,.5);}70%{box-shadow:0 0 0 8px rgba(207,138,42,0);}100%{box-shadow:0 0 0 0 rgba(207,138,42,0);}}

/* ---------- 타이포 ---------- */
.ed-h1{
  font-family:var(--sans); font-weight:800; letter-spacing:-.035em; line-height:1.08;
  font-size:clamp(2.7rem,6.4vw,5rem); margin:0;
}
.ed-h2{
  font-weight:800; letter-spacing:-.03em; line-height:1.16;
  font-size:clamp(1.9rem,3.6vw,2.9rem); margin:0;
}
.ed-quote{
  font-family:var(--serif); font-weight:400; line-height:1.42; letter-spacing:-.01em;
  font-size:clamp(1.5rem,3.2vw,2.5rem);
}
.ed-quote em{font-style:italic; color:var(--spark);}
.ed-lede{font-size:clamp(1rem,1.5vw,1.18rem); color:var(--ink-2); line-height:1.65;}
.ed-band--forest .ed-lede{color:rgba(233,228,207,.74);}

/* ---------- 버튼 / 링크 ---------- */
.ed-btn{
  display:inline-flex; align-items:center; gap:9px; cursor:pointer;
  font-family:var(--sans); font-size:.96rem; font-weight:800; letter-spacing:-.01em;
  padding:14px 24px; border:1px solid transparent; border-radius:5px;
  transition:transform .14s var(--ease),background .15s,opacity .15s;
}
.ed-btn:active{transform:translateY(1px);}
.ed-btn:disabled{opacity:.45; cursor:not-allowed;}
.ed-btn--ink{background:var(--ink); color:var(--paper);}
.ed-btn--moss{background:var(--moss); color:#fff;}
.ed-btn--moss:hover:not(:disabled){background:var(--moss-2);}
.ed-btn--line{background:transparent; color:var(--ink); border-color:var(--rule);}
.ed-btn--line:hover:not(:disabled){border-color:var(--ink);}
.ed-btn--spark{background:var(--spark); color:#231703;}
.ed-btn--lg{padding:17px 30px; font-size:1.02rem;}
.ed-btn--block{width:100%; justify-content:center;}
.ed-tlink{
  background:none; border:none; cursor:pointer; padding:0; font:inherit; color:inherit;
  font-weight:800; text-decoration:underline; text-underline-offset:5px; text-decoration-thickness:1.5px;
  text-decoration-color:var(--moss);
}
.ed-tlink:hover{text-decoration-color:currentColor;}

/* ---------- 상단 네비 ---------- */
.ed-nav{
  position:sticky; top:0; z-index:40; background:rgba(242,238,226,.9);
  backdrop-filter:saturate(150%) blur(12px); -webkit-backdrop-filter:saturate(150%) blur(12px);
  border-bottom:1px solid var(--rule);
}
.ed[data-theme="dark"] .ed-nav{background:rgba(20,19,13,.9);}
.ed-nav-in{
  max-width:1180px; margin:0 auto; padding:0 clamp(20px,5vw,60px); height:66px;
  display:flex; align-items:center; gap:10px;
}
.ed-mark{display:inline-flex; align-items:baseline; gap:0; cursor:pointer;}
.ed-mark b{font-family:var(--serif); font-style:italic; font-weight:700; font-size:1.5rem; letter-spacing:-.02em;}
.ed-mark i{width:7px; height:7px; border-radius:50%; background:var(--moss); margin-left:3px; align-self:flex-end; margin-bottom:5px; font-style:normal;}
.ed-nav-links{display:flex; gap:4px; margin-left:24px;}
.ed-nav-link{
  background:none; border:none; cursor:pointer; font-family:var(--sans);
  font-size:.92rem; font-weight:700; color:var(--ink-3); padding:8px 12px;
  border-bottom:2px solid transparent; transition:color .14s,border-color .14s;
}
.ed-nav-link:hover{color:var(--ink);}
.ed-nav-link--on{color:var(--ink); border-bottom-color:var(--moss);}
.ed-nav-right{margin-left:auto; display:flex; align-items:center; gap:10px;}
.ed-iconbtn{
  width:38px; height:38px; border:1px solid var(--rule); background:transparent; color:var(--ink-2);
  cursor:pointer; display:inline-flex; align-items:center; justify-content:center; border-radius:5px;
  transition:border-color .14s,color .14s;
}
.ed-iconbtn:hover{border-color:var(--ink); color:var(--ink);}
.ed-statuschip{
  display:inline-flex; align-items:center; gap:7px; height:38px; padding:0 14px;
  border:1px solid var(--rule); border-radius:5px; cursor:pointer;
  font-size:.8rem; font-weight:800; color:var(--ink-2); background:transparent;
}
.ed-statuschip--on{border-color:var(--moss); color:var(--moss);}
.ed-acct{display:inline-flex; align-items:center; gap:8px; cursor:pointer; background:none; border:none; color:inherit;}
.ed-acct-name{font-size:.88rem; font-weight:800;}

/* ---------- 인물 사진 ---------- */
.ed-portrait{border-radius:50%; object-fit:cover; background:var(--paper-2);}
.ed-portrait-fb{
  border-radius:50%; display:inline-flex; align-items:center; justify-content:center;
  font-family:var(--serif); font-weight:700; color:#fff; flex-shrink:0;
}

/* ---------- 히어로 ---------- */
.ed-hero{padding:clamp(40px,7vw,86px) 0 clamp(48px,8vw,96px);}
.ed-hero-grid{display:grid; grid-template-columns:1.12fr .88fr; gap:clamp(32px,5vw,72px); align-items:center;}
.ed-hero-h1 em{font-style:normal; position:relative; white-space:nowrap;}
.ed-hero-h1 em:after{
  content:""; position:absolute; left:0; right:0; bottom:.06em; height:.16em;
  background:var(--spark); opacity:.85; z-index:-1;
}
.ed-hero-lede{margin-top:22px; max-width:30em;}
.ed-hero-cta{display:flex; gap:14px; align-items:center; margin-top:30px; flex-wrap:wrap;}
.ed-hero-live{
  display:flex; align-items:center; gap:12px; margin-top:34px;
  padding-top:22px; border-top:1px solid var(--rule);
}
.ed-hero-live-fig{font-family:var(--serif); font-size:1.7rem; font-weight:700;}

/* 히어로 콜라주 — 손으로 흩어놓은 듯한 배치 */
.ed-collage{position:relative; min-height:380px;}
.ed-coll-card{
  position:absolute; background:var(--card); box-shadow:var(--lift);
  border:1px solid var(--rule);
}
.ed-coll-photo{
  top:0; left:8%; width:230px; padding:14px 14px 16px; transform:rotate(-4deg);
}
.ed-coll-photo img,.ed-coll-photo .ed-portrait-fb{width:100%; height:236px; border-radius:0; object-fit:cover;}
.ed-coll-cap{font-size:.82rem; color:var(--ink-2); margin-top:10px; font-weight:600;}
.ed-coll-quote{
  bottom:14%; right:0; width:250px; padding:18px; transform:rotate(3deg);
  font-family:var(--serif); font-size:1.04rem; line-height:1.5;
}
.ed-coll-quote .ed-qby{font-family:var(--sans); font-size:.78rem; font-weight:800; color:var(--ink-3); margin-top:10px;}
.ed-coll-tag{
  top:30%; right:6%; background:var(--ink); color:var(--paper);
  padding:11px 15px; transform:rotate(-7deg); box-shadow:var(--lift);
}
.ed-coll-tag b{font-family:var(--serif); font-size:1.3rem; font-weight:700;}
.ed-coll-tag span{display:block; font-size:.68rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase; opacity:.7;}

/* ---------- 신뢰 스트립 ---------- */
.ed-trust{display:flex; align-items:center; gap:20px; flex-wrap:wrap; padding:26px 0;}
.ed-faces{display:flex;}
.ed-faces > *{margin-left:-12px; box-shadow:0 0 0 3px var(--paper);}
.ed-faces > *:first-child{margin-left:0;}
.ed-trust-txt b{font-family:var(--serif); font-size:1.3rem; font-weight:700;}
.ed-trust-txt span{color:var(--ink-2); font-size:.92rem;}

/* ---------- 백테스트 ---------- */
.ed-bt-head{display:flex; justify-content:space-between; align-items:flex-end; gap:24px; flex-wrap:wrap;}
.ed-bt-tabs{display:flex; gap:0; border-bottom:1px solid var(--rule); margin:30px 0 8px;}
.ed-bt-tab{
  background:none; border:none; cursor:pointer; font-family:var(--sans);
  font-size:1rem; font-weight:800; color:var(--ink-3); padding:12px 4px; margin-right:28px;
  border-bottom:2px solid transparent; transition:color .14s,border-color .14s;
}
.ed-bt-tab--on{color:var(--ink); border-bottom-color:var(--moss);}
.ed-bt-stage{display:grid; grid-template-columns:1.5fr 1fr; gap:clamp(24px,4vw,52px); align-items:center; margin-top:26px;}
.ed-bt-legend{display:flex; gap:18px; flex-wrap:wrap; margin-bottom:12px;}
.ed-bt-leg{display:flex; align-items:center; gap:7px; font-size:.82rem; font-weight:700;}
.ed-bt-leg i{width:16px; height:3px; border-radius:2px;}
.ed-bt-figs{display:flex; flex-direction:column;}
.ed-bt-fig{
  display:flex; align-items:baseline; justify-content:space-between; gap:16px;
  padding:14px 0; border-bottom:1px solid var(--rule);
}
.ed-bt-fig:last-child{border-bottom:0;}
.ed-bt-fig-label{font-size:.86rem; font-weight:700; color:var(--ink-2);}
.ed-bt-fig-val{font-family:var(--serif); font-size:clamp(1.5rem,2.6vw,2.1rem); font-weight:700; letter-spacing:-.01em;}
.ed-chart{width:100%; height:auto; display:block;}

/* ---------- 작동 방식 ---------- */
.ed-step{
  display:grid; grid-template-columns:auto 1fr; gap:clamp(20px,4vw,48px); align-items:start;
  padding:clamp(28px,4vw,44px) 0; border-bottom:1px solid var(--rule);
}
.ed-step:last-child{border-bottom:0;}
.ed-step-no{font-family:var(--serif); font-size:clamp(3rem,7vw,5.4rem); font-weight:700; line-height:.85; color:var(--moss);}
.ed-step-h{font-size:clamp(1.3rem,2.4vw,1.8rem); font-weight:800; letter-spacing:-.025em; margin:0;}
.ed-step-p{margin:10px 0 0; color:var(--ink-2); max-width:34em; line-height:1.65;}

/* ---------- 후기 ---------- */
.ed-feature-q{display:grid; grid-template-columns:auto 1fr; gap:clamp(20px,4vw,40px); align-items:center;}
.ed-feature-q .ed-quote-mark{font-family:var(--serif); font-size:5rem; line-height:.7; color:var(--moss); height:.5em;}
.ed-q-meta{display:flex; align-items:center; gap:13px; margin-top:22px;}
.ed-q-name{font-weight:800;}
.ed-q-role{font-size:.84rem; color:var(--ink-3);}
.ed-q-row{display:grid; grid-template-columns:1fr 1fr; gap:clamp(20px,4vw,44px); margin-top:8px;}
.ed-q-small p{font-family:var(--serif); font-size:1.12rem; line-height:1.5; margin:0;}

/* ---------- 최종 CTA ---------- */
.ed-finale{text-align:center;}
.ed-finale .ed-h2{max-width:16em; margin:0 auto;}

/* ---------- 푸터 ---------- */
.ed-foot{border-top:1px solid var(--rule); padding:40px 0;}
.ed-foot-grid{display:flex; justify-content:space-between; gap:24px; flex-wrap:wrap; align-items:flex-start;}
.ed-fine{font-size:.78rem; color:var(--ink-3); line-height:1.7; max-width:42em;}

/* ---------- 앱: 운용 화면 ---------- */
.ed-app{padding:clamp(28px,5vw,52px) 0 100px;}
.ed-app-head{display:flex; justify-content:space-between; align-items:flex-end; gap:20px; flex-wrap:wrap; margin-bottom:8px;}
.ed-kicker{font-family:var(--serif); font-style:italic; font-size:1.16rem; color:var(--ink-3);}
.ed-app-h{font-size:clamp(1.7rem,3.2vw,2.5rem); font-weight:800; letter-spacing:-.03em; margin:4px 0 0;}

/* 봇 운용 밴드 */
.ed-runband{
  margin:26px 0; padding:clamp(22px,3.5vw,34px);
  display:flex; align-items:center; gap:26px; flex-wrap:wrap;
}
.ed-runband--on{background:var(--forest); color:var(--forest-ink);}
.ed-runband--off{background:var(--paper-2);}
.ed-runband-main{flex:1; min-width:240px;}
.ed-runband-h{font-size:clamp(1.4rem,2.6vw,2rem); font-weight:800; letter-spacing:-.03em; margin:10px 0 4px;}
.ed-runband-sub{font-size:.94rem;}
.ed-runband--on .ed-runband-sub{color:rgba(233,228,207,.72);}
.ed-runband--off .ed-runband-sub{color:var(--ink-2);}
.ed-runband-stats{display:flex; gap:30px; flex-wrap:wrap;}
.ed-runstat small{display:block; font-size:.74rem; font-weight:700; opacity:.66; margin-bottom:3px; letter-spacing:.04em;}
.ed-runstat b{font-family:var(--serif); font-size:1.5rem; font-weight:700;}
.ed-runbtn{
  cursor:pointer; font-family:var(--sans); font-size:1rem; font-weight:800;
  padding:16px 26px; border:0; border-radius:5px; display:inline-flex; align-items:center; gap:9px;
  transition:transform .14s var(--ease),filter .15s;
}
.ed-runbtn:active{transform:translateY(1px);}
.ed-runbtn--stop{background:rgba(255,255,255,.16); color:var(--forest-ink);}
.ed-runbtn--stop:hover{background:rgba(255,255,255,.26);}
.ed-runbtn--start{background:var(--moss); color:#fff;}
.ed-runbtn--start:hover{filter:brightness(1.08);}

/* 큰 숫자 (자산) */
.ed-figrow{display:flex; gap:clamp(24px,5vw,64px); flex-wrap:wrap; margin:8px 0 4px;}
.ed-fig{}
.ed-fig small{font-size:.8rem; font-weight:700; color:var(--ink-2); letter-spacing:.03em;}
.ed-fig b{display:block; font-family:var(--serif); font-weight:700; letter-spacing:-.02em; line-height:1.1;}
.ed-fig--xl b{font-size:clamp(2.4rem,5vw,3.6rem); margin-top:6px;}
.ed-fig--md b{font-size:clamp(1.5rem,2.6vw,2rem); margin-top:5px;}
.ed-fig-delta{font-size:.92rem; font-weight:800; margin-top:6px;}

/* 섹션 헤더 */
.ed-sec{margin-top:46px;}
.ed-sec-head{display:flex; align-items:baseline; justify-content:space-between; gap:14px; padding-bottom:12px; border-bottom:1.5px solid var(--ink); margin-bottom:4px;}
.ed-sec-title{font-size:1.16rem; font-weight:800; letter-spacing:-.02em;}
.ed-sec-link{background:none; border:none; cursor:pointer; font:inherit; color:var(--ink-3); font-weight:800; font-size:.84rem;}
.ed-sec-link:hover{color:var(--ink);}

/* 종목/노트 리스트 */
.ed-row{display:flex; align-items:center; gap:16px; padding:16px 2px; border-bottom:1px solid var(--rule);}
.ed-row:last-child{border-bottom:0;}
.ed-row-no{font-family:var(--serif); font-size:1.05rem; color:var(--ink-3); width:24px; flex-shrink:0;}
.ed-row-main{flex:1; min-width:0;}
.ed-row-name{font-weight:800; font-size:1rem;}
.ed-row-meta{font-size:.82rem; color:var(--ink-3); font-weight:600; margin-top:1px;}
.ed-row-num{text-align:right; flex-shrink:0;}
.ed-row-val{font-family:var(--serif); font-size:1.12rem; font-weight:700;}
.ed-row-pl{font-size:.82rem; font-weight:800; margin-top:1px;}

/* AI 노트 */
.ed-note{display:flex; gap:14px; padding:16px 2px; border-bottom:1px solid var(--rule);}
.ed-note:last-child{border-bottom:0;}
.ed-note-tick{width:34px; flex-shrink:0; font-family:var(--serif); font-style:italic; font-weight:700; color:var(--moss);}
.ed-note p{margin:0; line-height:1.6;}
.ed-note p b{font-weight:800;}
.ed-note time{display:block; font-size:.76rem; color:var(--ink-3); font-weight:700; margin-top:5px;}

/* 미니 차트 */
.ed-mini{width:100%; height:auto; display:block; margin:14px 0 4px;}

/* 전략 칼럼 */
.ed-strat-cols{display:grid; grid-template-columns:repeat(3,1fr); gap:0;}
.ed-strat-col{
  padding:clamp(20px,2.5vw,30px) clamp(16px,2vw,26px); cursor:pointer; position:relative;
  border-top:3px solid transparent; transition:background .15s;
}
.ed-strat-col + .ed-strat-col{border-left:1px solid var(--rule);}
.ed-strat-col:hover{background:var(--card);}
.ed-strat-col--on{border-top-color:var(--moss); background:var(--card);}
.ed-strat-no{font-family:var(--serif); font-size:2.4rem; font-weight:700; line-height:1; color:var(--ink-3);}
.ed-strat-col--on .ed-strat-no{color:var(--moss);}
.ed-strat-name{font-size:1.35rem; font-weight:800; letter-spacing:-.025em; margin-top:14px;}
.ed-strat-tag{font-family:var(--serif); font-style:italic; color:var(--ink-3); margin-top:2px;}
.ed-strat-desc{font-size:.88rem; color:var(--ink-2); line-height:1.6; margin:14px 0;}
.ed-strat-m{display:flex; justify-content:space-between; font-size:.86rem; padding:8px 0; border-bottom:1px solid var(--rule);}
.ed-strat-m span{color:var(--ink-3); font-weight:700;}
.ed-strat-m b{font-family:var(--serif); font-weight:700; font-size:1.02rem;}
.ed-strat-pick{
  margin-top:16px; font-weight:800; font-size:.9rem; display:flex; align-items:center; gap:7px; color:var(--ink-3);
}
.ed-strat-col--on .ed-strat-pick{color:var(--moss);}

/* 필터 */
.ed-filters{display:flex; gap:22px; border-bottom:1px solid var(--rule); margin-bottom:4px;}
.ed-filter{
  background:none; border:none; cursor:pointer; font:inherit; font-weight:800; color:var(--ink-3);
  padding:10px 2px; border-bottom:2px solid transparent;
}
.ed-filter--on{color:var(--ink); border-bottom-color:var(--ink);}
.ed-daterow{font-family:var(--serif); font-style:italic; color:var(--ink-3); margin:24px 0 4px;}

/* 거래 행 */
.ed-trade{display:flex; align-items:center; gap:16px; padding:15px 2px; border-bottom:1px solid var(--rule);}
.ed-trade:last-child{border-bottom:0;}
.ed-trade-side{font-family:var(--serif); font-weight:700; font-size:.92rem; width:54px; flex-shrink:0;}
.ed-trade-side--buy{color:var(--up);}
.ed-trade-side--sell{color:var(--down);}

/* 설정 */
.ed-set-row{
  display:flex; align-items:center; gap:16px; width:100%; padding:18px 2px;
  border:0; border-bottom:1px solid var(--rule); background:none; cursor:pointer; color:inherit; text-align:left;
}
.ed-set-row:last-child{border-bottom:0;}
.ed-set-row--static{cursor:default;}
.ed-set-body{flex:1; min-width:0;}
.ed-set-label{font-weight:800; font-size:1rem;}
.ed-set-desc{font-size:.82rem; color:var(--ink-3); margin-top:1px;}
.ed-set-val{font-family:var(--serif); font-weight:700; font-size:1.05rem; color:var(--ink-2);}
.ed-seg{display:flex; gap:0; border:1px solid var(--rule);}
.ed-seg-btn{
  background:none; border:none; cursor:pointer; font:inherit; font-weight:800; font-size:.86rem;
  color:var(--ink-3); padding:9px 16px;
}
.ed-seg-btn + .ed-seg-btn{border-left:1px solid var(--rule);}
.ed-seg-btn--on{background:var(--ink); color:var(--paper);}
.ed-switch{
  width:46px; height:26px; border-radius:13px; background:var(--rule); border:0; cursor:pointer;
  position:relative; flex-shrink:0; transition:background .18s;
}
.ed-switch--on{background:var(--moss);}
.ed-knob{position:absolute; top:3px; left:3px; width:20px; height:20px; border-radius:50%; background:#fff; transition:transform .18s var(--ease);}
.ed-switch--on .ed-knob{transform:translateX(20px);}
.ed-broker{display:flex; align-items:center; gap:14px;}
.ed-broker-mk{
  width:46px; height:46px; background:#f5a623; color:#fff; flex-shrink:0;
  display:inline-flex; align-items:center; justify-content:center; font-weight:800; font-size:.82rem;
}

/* ---------- 온보딩 ---------- */
.ed-ob{max-width:560px; margin:0 auto; padding:clamp(28px,6vw,64px) 0 80px;}
.ed-ob-top{display:flex; align-items:center; justify-content:space-between; margin-bottom:34px;}
.ed-ob-count{font-family:var(--serif); font-size:1.1rem; font-weight:700;}
.ed-ob-count em{font-style:normal; color:var(--ink-3);}
.ed-ob-track{height:2px; background:var(--rule); margin-bottom:34px;}
.ed-ob-fill{height:100%; background:var(--ink); transition:width .45s var(--ease);}
.ed-ob-q{font-size:clamp(1.6rem,3.4vw,2.3rem); font-weight:800; letter-spacing:-.03em; line-height:1.22; margin:0;}
.ed-ob-hint{color:var(--ink-2); margin:12px 0 0; line-height:1.6;}
.ed-ob-opts{margin:28px 0;}
.ed-ob-opt{
  display:flex; align-items:center; gap:16px; width:100%; padding:18px 4px; cursor:pointer;
  background:none; border:0; border-bottom:1px solid var(--rule); color:inherit; text-align:left;
}
.ed-ob-opt:first-child{border-top:1px solid var(--rule);}
.ed-ob-opt-no{font-family:var(--serif); font-size:1.1rem; color:var(--ink-3); width:22px;}
.ed-ob-opt--on .ed-ob-opt-no{color:var(--moss);}
.ed-ob-opt-body{flex:1; min-width:0;}
.ed-ob-opt-t{font-weight:800; font-size:1.06rem;}
.ed-ob-opt-s{font-size:.86rem; color:var(--ink-3); margin-top:1px;}
.ed-ob-mark{
  width:26px; height:26px; border-radius:50%; border:1.5px solid var(--rule); flex-shrink:0;
  display:inline-flex; align-items:center; justify-content:center; color:transparent;
}
.ed-ob-opt--on .ed-ob-mark{background:var(--moss); border-color:var(--moss); color:#fff;}
.ed-ob-input{
  width:100%; background:none; border:0; border-bottom:2px solid var(--rule); color:inherit;
  font-family:var(--serif); font-size:2.4rem; font-weight:700; padding:8px 0; outline:none;
  transition:border-color .15s;
}
.ed-ob-input:focus{border-bottom-color:var(--moss);}
.ed-ob-chips{display:flex; gap:8px; flex-wrap:wrap; margin-top:18px;}
.ed-ob-chip{
  background:none; border:1px solid var(--rule); cursor:pointer; font:inherit; font-weight:700;
  font-size:.86rem; padding:8px 14px; border-radius:4px; color:var(--ink-2);
}
.ed-ob-chip--on{background:var(--ink); color:var(--paper); border-color:var(--ink);}
.ed-ob-sum{border-top:1px solid var(--rule); margin:20px 0;}
.ed-ob-sum-row{display:flex; justify-content:space-between; padding:13px 0; border-bottom:1px solid var(--rule); font-size:.92rem;}
.ed-ob-sum-row span:first-child{color:var(--ink-3); font-weight:700;}
.ed-ob-sum-row span:last-child{font-weight:800;}

.ed-fade{animation:ed-fade .5s var(--ease) both;}
@keyframes ed-fade{from{opacity:0; transform:translateY(14px);}to{opacity:1; transform:translateY(0);}}

/* ---------- 반응형 ---------- */
@media (max-width:920px){
  .ed-hero-grid{grid-template-columns:1fr; gap:38px;}
  .ed-collage{min-height:330px; max-width:420px;}
  .ed-bt-stage{grid-template-columns:1fr; gap:30px;}
  .ed-feature-q{grid-template-columns:1fr; gap:8px;}
  .ed-q-row{grid-template-columns:1fr; gap:22px;}
}
@media (max-width:680px){
  .ed-nav-in{padding:0 16px; gap:6px;}
  .ed-acct{display:none;}
  .ed-statuschip span{display:none;}
  .ed-nav-links{margin-left:4px; gap:0;}
  .ed-nav-link{padding:7px 7px; font-size:.85rem;}
  .ed-strat-cols{grid-template-columns:1fr;}
  .ed-strat-col + .ed-strat-col{border-left:0; border-top:1px solid var(--rule);}
  .ed-step{grid-template-columns:1fr; gap:6px;}
  .ed-bt-tab{margin-right:18px;}
}
@media (prefers-reduced-motion:reduce){
  .ed *{animation-duration:.001ms !important; transition-duration:.001ms !important;}
}
`;

/* ============================================================
   2. 아이콘 (얇은 라인)
   ============================================================ */
type IconProps = SVGProps<SVGSVGElement> & { size?: number };
function Icon({ size = 20, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}
const ArrowRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 12h15M13 5l7 7-7 7" />
  </Icon>
);
const ArrowLeft = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 12H5M11 5l-7 7 7 7" />
  </Icon>
);
const CheckIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 6.5 9.2 17.5 4 12.3" />
  </Icon>
);
const PlayIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 4.5v15l13-7.5L7 4.5Z" />
  </Icon>
);
const PauseIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 4.5v15M15 4.5v15" />
  </Icon>
);
const SunIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="4.2" />
    <path d="M12 2v2.4M12 19.6V22M2 12h2.4M19.6 12H22M4.7 4.7l1.7 1.7M17.6 17.6l1.7 1.7M4.7 19.3l1.7-1.7M17.6 6.4l1.7-1.7" />
  </Icon>
);
const MoonIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
  </Icon>
);
const BellIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M18 9a6 6 0 1 0-12 0c0 6-2.5 7.5-2.5 7.5h17S18 15 18 9Z" />
    <path d="M10 20a2.4 2.4 0 0 0 4 0" />
  </Icon>
);
const ShieldIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3 5 6v6c0 4.5 3 7.8 7 9 4-1.2 7-4.5 7-9V6l-7-3Z" />
    <path d="m9 12 2 2 4-4" />
  </Icon>
);
const SlidersIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 21V14M5 10V3M12 21v-9M12 8V3M19 21v-5M19 12V3" />
    <circle cx="5" cy="12" r="2.1" />
    <circle cx="12" cy="10" r="2.1" />
    <circle cx="19" cy="14" r="2.1" />
  </Icon>
);
const FileIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
    <path d="M13 3v6h6M9 13h7M9 17h5" />
  </Icon>
);
const WalletIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 7a2 2 0 0 1 2-2h12v4" />
    <path d="M3 7v10a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1H5" />
    <circle cx="16.5" cy="13.5" r="1.3" />
  </Icon>
);
const LogoutIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4" />
    <path d="M10 8 6 12l4 4M6 12h9" />
  </Icon>
);
const SparkIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 4.5 13.6 9 18 10.6 13.6 12.2 12 16.7 10.4 12.2 6 10.6 10.4 9 12 4.5Z" />
  </Icon>
);

/* ============================================================
   3. 타입 · 데이터 · 포맷
   ============================================================ */
type Theme = "light" | "dark";
type Route = "landing" | "onboarding" | "app";
type AppTab = "run" | "strategy" | "history" | "settings";
type StrategyId = "stable" | "balanced" | "aggressive";

function won(v: number) {
  return "₩" + Math.round(v).toLocaleString("ko-KR");
}
function signedWon(v: number) {
  const s = v > 0 ? "+" : v < 0 ? "-" : "";
  return s + "₩" + Math.abs(Math.round(v)).toLocaleString("ko-KR");
}
function signedPct(v: number) {
  const s = v > 0 ? "+" : v < 0 ? "-" : "";
  return s + Math.abs(v).toFixed(2) + "%";
}
function tone(v: number) {
  return v > 0 ? "ed-up" : v < 0 ? "ed-down" : "";
}

const user = { name: "강록", email: "mangotree0867@gmail.com", initial: "강", photo: "https://randomuser.me/api/portraits/men/85.jpg" };

interface Strategy {
  id: StrategyId;
  no: string;
  name: string;
  tagline: string;
  targetReturn: string;
  mdd: string;
  frequency: string;
  riskLevel: 1 | 2 | 3;
  description: string;
}
const strategies: Strategy[] = [
  {
    id: "stable",
    no: "01",
    name: "안정형",
    tagline: "느긋하게, 단단하게",
    targetReturn: "6–9%",
    mdd: "-8%",
    frequency: "낮음",
    riskLevel: 1,
    description: "우량 대형주를 천천히 나눠 담고, 흔들릴 때 빠르게 한 발 물러섭니다. 마음 편한 운용이에요.",
  },
  {
    id: "balanced",
    no: "02",
    name: "균형형",
    tagline: "수익과 안심, 그 사이",
    targetReturn: "12–18%",
    mdd: "-15%",
    frequency: "보통",
    riskLevel: 2,
    description: "성장주와 가치주를 함께 품고 시장의 결을 따라갑니다. 대부분의 분께 잘 맞습니다.",
  },
  {
    id: "aggressive",
    no: "03",
    name: "공격형",
    tagline: "기회가 보이면 과감하게",
    targetReturn: "25–40%",
    mdd: "-30%",
    frequency: "높음",
    riskLevel: 3,
    description: "모멘텀이 강한 종목에 집중하고 자주 움직입니다. 변동을 견딜 여유 자금으로 함께해요.",
  },
];

/* --- 백테스트 데이터 (2014.05–2024.05, 지수 100 기준) --- */
const BT_BENCH = [100, 108, 113, 106, 119, 131, 123, 111, 117, 129, 96, 133, 146, 151, 139, 121, 129, 136, 131, 139, 141];
const BT: Record<StrategyId, { curve: number[]; total: string; cagr: string; mdd: string; sharpe: string; win: string }> = {
  stable: {
    curve: [100, 104, 108, 107, 113, 119, 121, 118, 124, 131, 121, 138, 146, 152, 157, 160, 165, 171, 178, 186, 194],
    total: "+94%",
    cagr: "6.9%",
    mdd: "-7.9%",
    sharpe: "1.51",
    win: "71%",
  },
  balanced: {
    curve: [100, 106, 113, 109, 121, 134, 138, 130, 141, 156, 128, 162, 181, 196, 188, 178, 201, 224, 240, 262, 282],
    total: "+182%",
    cagr: "10.9%",
    mdd: "-15.2%",
    sharpe: "1.34",
    win: "64%",
  },
  aggressive: {
    curve: [100, 110, 124, 116, 138, 162, 170, 150, 172, 205, 150, 212, 258, 300, 270, 238, 295, 350, 386, 425, 461],
    total: "+361%",
    cagr: "16.5%",
    mdd: "-29.4%",
    sharpe: "1.08",
    win: "58%",
  },
};

const account = {
  totalValue: 24_817_400,
  dayChange: 312_600,
  dayChangePct: 1.28,
  cash: 6_240_000,
  realizedToday: 184_200,
  unrealized: 1_046_900,
  unrealizedPct: 5.96,
};
const equityCurve = [23.05, 22.98, 23.4, 23.18, 23.82, 24.0, 23.68, 24.12, 24.3, 24.18, 24.02, 24.41, 24.6, 24.82];

interface Position {
  code: string;
  name: string;
  shares: number;
  avgPrice: number;
  currentPrice: number;
}
const positions: Position[] = [
  { code: "005930", name: "삼성전자", shares: 42, avgPrice: 71_200, currentPrice: 78_400 },
  { code: "000660", name: "SK하이닉스", shares: 18, avgPrice: 168_000, currentPrice: 182_500 },
  { code: "035420", name: "NAVER", shares: 24, avgPrice: 215_000, currentPrice: 204_500 },
  { code: "373220", name: "LG에너지솔루션", shares: 6, avgPrice: 402_000, currentPrice: 421_000 },
  { code: "035720", name: "카카오", shares: 30, avgPrice: 52_000, currentPrice: 47_800 },
];
function positionPL(p: Position) {
  const cost = p.avgPrice * p.shares;
  const value = p.currentPrice * p.shares;
  return { value, pl: value - cost, pct: ((value - cost) / cost) * 100 };
}

const signals = [
  { text: "반도체 흐름이 좋아 <b>SK하이닉스</b>를 5% 더 담았어요.", time: "방금 전" },
  { text: "<b>카카오</b>가 손절선에 가까워져 비중을 절반으로 줄였어요.", time: "32분 전" },
  { text: "시장 변동이 커져 현금 비중을 25%까지 늘려뒀습니다.", time: "1시간 전" },
];

interface Trade {
  id: string;
  date: string;
  time: string;
  side: "buy" | "sell";
  name: string;
  shares: number;
  price: number;
  realizedPL?: number;
}
const trades: Trade[] = [
  { id: "t1", date: "오늘 · 5월 22일", time: "14:02", side: "buy", name: "SK하이닉스", shares: 3, price: 182_500 },
  { id: "t2", date: "오늘 · 5월 22일", time: "11:18", side: "sell", name: "기아", shares: 12, price: 118_700, realizedPL: 96_400 },
  { id: "t3", date: "오늘 · 5월 22일", time: "09:31", side: "buy", name: "삼성전자", shares: 8, price: 77_900 },
  { id: "t4", date: "어제 · 5월 21일", time: "15:09", side: "sell", name: "셀트리온", shares: 5, price: 192_300, realizedPL: -41_800 },
  { id: "t5", date: "어제 · 5월 21일", time: "10:44", side: "buy", name: "NAVER", shares: 6, price: 203_000 },
  { id: "t6", date: "5월 20일", time: "14:55", side: "sell", name: "현대차", shares: 9, price: 248_500, realizedPL: 129_300 },
  { id: "t7", date: "5월 20일", time: "09:48", side: "buy", name: "LG에너지솔루션", shares: 2, price: 399_000 },
];
const monthly = { realized: 1_284_600, count: 47, winRate: 64, days: 87 };

/* --- 후기 인물 (실제 사진: randomuser.me) --- */
interface Person {
  name: string;
  role: string;
  quote: string;
  photo: string;
}
const featured: Person = {
  name: "정유진",
  role: "38세 · 콘텐츠 마케터",
  quote: "퇴근하고 차트만 들여다보던 시간이 사라졌어요. 그 시간에 이제 아이와 저녁을 먹습니다.",
  photo: "https://randomuser.me/api/portraits/women/65.jpg",
};
const reviews: Person[] = [
  {
    name: "한승호",
    role: "45세 · 자영업",
    quote: "감정적으로 사고팔던 버릇이 없어졌습니다. 규칙대로 움직이니 마음이 놓여요.",
    photo: "https://randomuser.me/api/portraits/men/41.jpg",
  },
  {
    name: "오세라",
    role: "31세 · 소프트웨어 개발자",
    quote: "10년 백테스트를 직접 보고 나서야 믿음이 생겼어요. 숫자가 솔직하더라고요.",
    photo: "https://randomuser.me/api/portraits/women/29.jpg",
  },
];
const heroFace: Person = {
  name: "이도현",
  role: "균형형 운용 · 7개월째",
  quote: "주말엔 휴대폰을 안 봐요. 그래도 잘 굴러갑니다.",
  photo: "https://randomuser.me/api/portraits/men/76.jpg",
};
const trustFaces = [
  "https://randomuser.me/api/portraits/women/12.jpg",
  "https://randomuser.me/api/portraits/men/52.jpg",
  "https://randomuser.me/api/portraits/women/33.jpg",
  "https://randomuser.me/api/portraits/men/8.jpg",
  "https://randomuser.me/api/portraits/women/50.jpg",
  "https://randomuser.me/api/portraits/men/63.jpg",
];

/* ============================================================
   4. 인물 사진 — 실패 시 이니셜로 우아하게 폴백
   ============================================================ */
const FB_COLORS = ["#1f7a4f", "#bc3a2c", "#2f59b6", "#cf8a2a", "#6b4ea8", "#177d86"];
function Portrait({
  src,
  name,
  size,
  square = false,
}: {
  src: string;
  name: string;
  size: number;
  square?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const color = FB_COLORS[name.charCodeAt(0) % FB_COLORS.length];
  if (failed) {
    return (
      <span
        className={square ? "" : "ed-portrait-fb"}
        style={{
          width: size,
          height: size,
          background: color,
          fontSize: size * 0.4,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#fff",
          fontFamily: "var(--serif)",
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        {name.slice(0, 1)}
      </span>
    );
  }
  return (
    <img
      src={src}
      alt={name}
      width={size}
      height={size}
      className={square ? "" : "ed-portrait"}
      style={{ width: size, height: size, objectFit: "cover" }}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

/* ============================================================
   5. 차트
   ============================================================ */
function buildPath(vals: number[], w: number, h: number, lo: number, hi: number, pad: number) {
  const range = hi - lo || 1;
  return vals
    .map((v, i) => {
      const x = pad + (i / (vals.length - 1)) * (w - pad * 2);
      const y = pad + (1 - (v - lo) / range) * (h - pad * 2);
      return `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

/* 백테스트 — 전략 vs 코스피 */
function BacktestChart({ curve }: { curve: number[] }) {
  const w = 720;
  const h = 300;
  const pad = 8;
  const hi = Math.max(...curve, ...BT_BENCH);
  const lo = Math.min(...curve, ...BT_BENCH, 80);
  const stratLine = buildPath(curve, w, h, lo, hi, pad);
  const benchLine = buildPath(BT_BENCH, w, h, lo, hi, pad);
  const last = curve[curve.length - 1];
  const lastX = w - pad;
  const lastY = pad + (1 - (last - lo) / (hi - lo || 1)) * (h - pad * 2);
  return (
    <svg className="ed-chart" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="백테스트 수익 곡선">
      <defs>
        <linearGradient id="ed-bt" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--moss)" stopOpacity="0.18" />
          <stop offset="100%" stopColor="var(--moss)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0, 0.5, 1].map((g) => (
        <line key={g} x1={pad} x2={w - pad} y1={pad + g * (h - pad * 2)} y2={pad + g * (h - pad * 2)} stroke="var(--rule)" strokeWidth={1} />
      ))}
      <path d={`${stratLine} L${lastX} ${h - pad} L${pad} ${h - pad} Z`} fill="url(#ed-bt)" />
      <path d={benchLine} fill="none" stroke="var(--ink-3)" strokeWidth={2} strokeDasharray="5 5" />
      <path d={stratLine} fill="none" stroke="var(--moss)" strokeWidth={3.4} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lastX} cy={lastY} r={5} fill="var(--moss)" />
    </svg>
  );
}

/* 운용 화면 미니 차트 */
function MiniChart({ data }: { data: number[] }) {
  const w = 560;
  const h = 120;
  const pad = 5;
  const hi = Math.max(...data);
  const lo = Math.min(...data);
  const line = buildPath(data, w, h, lo, hi, pad);
  const lx = w - pad;
  const ly = pad + (1 - (data[data.length - 1] - lo) / (hi - lo || 1)) * (h - pad * 2);
  return (
    <svg className="ed-mini" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="ed-mini" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--moss)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--moss)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${lx} ${h} L${pad} ${h} Z`} fill="url(#ed-mini)" />
      <path d={line} fill="none" stroke="var(--moss)" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={4} fill="var(--moss)" />
    </svg>
  );
}

/* ============================================================
   6. 공통
   ============================================================ */
function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  return (
    <button type="button" className="ed-iconbtn" onClick={onToggle} aria-label={theme === "dark" ? "낮 모드" : "밤 모드"}>
      {theme === "dark" ? <SunIcon size={17} /> : <MoonIcon size={17} />}
    </button>
  );
}
function Switch({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`ed-switch${on ? " ed-switch--on" : ""}`} onClick={onClick} role="switch" aria-checked={on}>
      <span className="ed-knob" />
    </button>
  );
}
function Wordmark() {
  return (
    <span className="ed-mark">
      <b>HQA</b>
      <i />
    </span>
  );
}

const APP_TABS: { id: AppTab; label: string }[] = [
  { id: "run", label: "운용" },
  { id: "strategy", label: "전략" },
  { id: "history", label: "기록" },
  { id: "settings", label: "설정" },
];

function AppNav({
  tab,
  onTab,
  theme,
  onToggleTheme,
  botRunning,
  onToggleBot,
}: {
  tab: AppTab;
  onTab: (t: AppTab) => void;
  theme: Theme;
  onToggleTheme: () => void;
  botRunning: boolean;
  onToggleBot: () => void;
}) {
  return (
    <nav className="ed-nav">
      <div className="ed-nav-in">
        <span onClick={() => onTab("run")} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onTab("run")}>
          <Wordmark />
        </span>
        <div className="ed-nav-links">
          {APP_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`ed-nav-link${tab === t.id ? " ed-nav-link--on" : ""}`}
              onClick={() => onTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="ed-nav-right">
          <button
            type="button"
            className={`ed-statuschip${botRunning ? " ed-statuschip--on" : ""}`}
            onClick={onToggleBot}
          >
            <span className={`ed-dot${botRunning ? " ed-dot--live" : ""}`} />
            <span>{botRunning ? "운용 중" : "정지"}</span>
          </button>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
          <button type="button" className="ed-acct" onClick={() => onTab("settings")}>
            <Portrait src={user.photo} name={user.name} size={32} />
            <span className="ed-acct-name">{user.name}</span>
          </button>
        </div>
      </div>
    </nav>
  );
}

/* ============================================================
   7. 랜딩 (홈)
   ============================================================ */
function Landing({
  theme,
  onToggleTheme,
  onStart,
  onLogin,
}: {
  theme: Theme;
  onToggleTheme: () => void;
  onStart: () => void;
  onLogin: () => void;
}) {
  const [btSel, setBtSel] = useState<StrategyId>("balanced");
  const bt = BT[btSel];
  const btName = strategies.find((s) => s.id === btSel)!.name;

  return (
    <div className="ed-fade">
      {/* 네비 */}
      <nav className="ed-nav">
        <div className="ed-nav-in">
          <Wordmark />
          <div className="ed-nav-right">
            <ThemeToggle theme={theme} onToggle={onToggleTheme} />
            <button type="button" className="ed-tlink" style={{ fontSize: ".9rem" }} onClick={onLogin}>
              로그인
            </button>
            <button type="button" className="ed-btn ed-btn--ink" onClick={onStart}>
              시작하기
            </button>
          </div>
        </div>
      </nav>

      {/* 히어로 */}
      <header className="ed-wrap ed-hero">
        <div className="ed-hero-grid">
          <div>
            <span className="ed-eyebrow">
              <span className="ed-dot" /> AI 주식 자동매매 · 2026
            </span>
            <h1 className="ed-h1 ed-hero-h1" style={{ marginTop: 22 }}>
              당신이 사는 동안,
              <br />
              돈은 <em>일하게</em>.
            </h1>
            <p className="ed-lede ed-hero-lede">
              HQA의 AI가 한국 증시를 24시간 지켜보며, 강록님이 정한 전략대로
              직접 사고팝니다. 당신은 그저 일상을 살아가면 됩니다.
            </p>
            <div className="ed-hero-cta">
              <button type="button" className="ed-btn ed-btn--moss ed-btn--lg" onClick={onStart}>
                3분 만에 시작하기 <ArrowRight size={18} />
              </button>
              <button type="button" className="ed-tlink" onClick={onLogin}>
                먼저 둘러보기
              </button>
            </div>
            <div className="ed-hero-live">
              <span className="ed-dot ed-dot--live" />
              <span>
                <span className="ed-hero-live-fig ed-up">+1.28%</span>
                <span style={{ color: "var(--ink-3)", marginLeft: 8, fontWeight: 600, fontSize: ".9rem" }}>
                  지금 운용 중인 자산의 오늘 수익률
                </span>
              </span>
            </div>
          </div>

          {/* 콜라주 — 흩어놓은 듯한 배치 */}
          <div className="ed-collage" aria-hidden="false">
            <figure className="ed-coll-card ed-coll-photo" style={{ margin: 0 }}>
              <Portrait src={heroFace.photo} name={heroFace.name} size={202} square />
              <figcaption className="ed-coll-cap">
                <b style={{ color: "var(--ink)", fontWeight: 800 }}>{heroFace.name}</b> · {heroFace.role}
              </figcaption>
            </figure>
            <div className="ed-coll-card ed-coll-tag">
              <span>오늘 수익</span>
              <b>+₩312,600</b>
            </div>
            <blockquote className="ed-coll-card ed-coll-quote" style={{ margin: 0 }}>
              “{heroFace.quote}”
              <div className="ed-qby">— 실사용자 후기</div>
            </blockquote>
          </div>
        </div>

        {/* 신뢰 스트립 */}
        <hr className="ed-rule" style={{ marginTop: 48 }} />
        <div className="ed-trust">
          <div className="ed-faces">
            {trustFaces.map((src, i) => (
              <Portrait key={i} src={src} name={`투자자${i}`} size={42} />
            ))}
          </div>
          <div className="ed-trust-txt">
            <b>12,400명</b>
            <span style={{ marginLeft: 10 }}>의 투자자가 HQA에 운용을 맡기고 있습니다.</span>
          </div>
        </div>
      </header>

      {/* 철학 밴드 */}
      <section className="ed-band ed-band--forest">
        <div className="ed-wrap">
          <span className="ed-label">우리의 생각</span>
          <p className="ed-quote" style={{ marginTop: 20, maxWidth: "20em" }}>
            투자는 불안한 일이 아니라, <em>안심하고 맡기는 일</em>이어야 합니다.
            그래서 우리는 화려한 숫자판 대신, 곁에서 차분히 돌봐주는 도구를
            만듭니다.
          </p>
        </div>
      </section>

      {/* 백테스트 */}
      <section className="ed-wrap ed-band">
        <div className="ed-bt-head">
          <div>
            <span className="ed-label">백테스트 · 2014.05 — 2024.05</span>
            <h2 className="ed-h2" style={{ marginTop: 14 }}>
              지난 10년에
              <br />
              직접 적용해 봤습니다.
            </h2>
          </div>
          <p className="ed-lede" style={{ maxWidth: "22em" }}>
            말보다 숫자가 정직합니다. 같은 기간 코스피와 나란히 두고 확인하세요.
          </p>
        </div>

        <div className="ed-bt-tabs" role="tablist">
          {strategies.map((s) => (
            <button
              key={s.id}
              type="button"
              role="tab"
              aria-selected={btSel === s.id}
              className={`ed-bt-tab${btSel === s.id ? " ed-bt-tab--on" : ""}`}
              onClick={() => setBtSel(s.id)}
            >
              {s.name}
            </button>
          ))}
        </div>

        <div className="ed-bt-stage">
          <div>
            <div className="ed-bt-legend">
              <span className="ed-bt-leg">
                <i style={{ background: "var(--moss)" }} /> HQA {btName}
              </span>
              <span className="ed-bt-leg">
                <i style={{ background: "var(--ink-3)" }} /> 코스피 지수
              </span>
            </div>
            <BacktestChart curve={bt.curve} />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: ".76rem", color: "var(--ink-3)", fontWeight: 700, marginTop: 6 }}>
              <span>2014</span>
              <span>2019</span>
              <span>2024</span>
            </div>
          </div>

          <div className="ed-bt-figs">
            <div className="ed-bt-fig">
              <span className="ed-bt-fig-label">누적 수익률</span>
              <span className="ed-bt-fig-val ed-up">{bt.total}</span>
            </div>
            <div className="ed-bt-fig">
              <span className="ed-bt-fig-label">연평균 (CAGR)</span>
              <span className="ed-bt-fig-val">{bt.cagr}</span>
            </div>
            <div className="ed-bt-fig">
              <span className="ed-bt-fig-label">최대 낙폭 (MDD)</span>
              <span className="ed-bt-fig-val ed-down">{bt.mdd}</span>
            </div>
            <div className="ed-bt-fig">
              <span className="ed-bt-fig-label">샤프 지수</span>
              <span className="ed-bt-fig-val">{bt.sharpe}</span>
            </div>
            <div className="ed-bt-fig">
              <span className="ed-bt-fig-label">승률</span>
              <span className="ed-bt-fig-val">{bt.win}</span>
            </div>
          </div>
        </div>
        <p className="ed-fine" style={{ marginTop: 22 }}>
          동기간 코스피 지수는 +41%였습니다. 백테스트 결과는 과거 데이터를
          기반으로 한 시뮬레이션이며, 미래 수익을 보장하지 않습니다.
        </p>
      </section>

      {/* 작동 방식 */}
      <section className="ed-band ed-band--paper2">
        <div className="ed-wrap">
          <span className="ed-label">맡기는 과정</span>
          <h2 className="ed-h2" style={{ marginTop: 14, marginBottom: 14 }}>
            세 걸음이면 충분합니다.
          </h2>
          {[
            ["01", "연결합니다", "한국투자증권 계좌를 Open API로 안전하게 연결해요. 매매 권한만 쓰고 출금은 불가능합니다."],
            ["02", "맡깁니다", "투자 성향과 한도, 손실 기준을 정하면 AI가 그 안에서 전략을 세웁니다."],
            ["03", "살아갑니다", "그다음은 온전히 당신의 시간. AI가 사고팔며 매일 자산을 돌봅니다."],
          ].map(([no, h, p]) => (
            <div className="ed-step" key={no}>
              <div className="ed-step-no">{no}</div>
              <div>
                <h3 className="ed-step-h">{h}</h3>
                <p className="ed-step-p">{p}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 후기 — 실제 인물 */}
      <section className="ed-wrap ed-band">
        <span className="ed-label">함께하는 사람들</span>
        <h2 className="ed-h2" style={{ marginTop: 14, marginBottom: 34 }}>
          맡긴 사람들의 이야기.
        </h2>

        <div className="ed-feature-q">
          <Portrait src={featured.photo} name={featured.name} size={150} />
          <div>
            <p className="ed-quote">“{featured.quote}”</p>
            <div className="ed-q-meta">
              <span>
                <span className="ed-q-name">{featured.name}</span>
                <span className="ed-q-role" style={{ display: "block" }}>
                  {featured.role}
                </span>
              </span>
            </div>
          </div>
        </div>

        <hr className="ed-rule" style={{ margin: "38px 0" }} />

        <div className="ed-q-row">
          {reviews.map((r) => (
            <div className="ed-q-small" key={r.name}>
              <p>“{r.quote}”</p>
              <div className="ed-q-meta">
                <Portrait src={r.photo} name={r.name} size={48} />
                <span>
                  <span className="ed-q-name">{r.name}</span>
                  <span className="ed-q-role" style={{ display: "block" }}>
                    {r.role}
                  </span>
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 최종 CTA */}
      <section className="ed-band ed-band--forest">
        <div className="ed-wrap ed-finale">
          <span className="ed-label">시작하기</span>
          <h2 className="ed-h2" style={{ margin: "16px auto 0" }}>
            오늘부터, 투자에서 한 발 물러나 보세요.
          </h2>
          <p className="ed-lede" style={{ margin: "14px auto 26px", maxWidth: "26em" }}>
            설정은 3분. 모의투자로 충분히 연습한 뒤 실전으로 옮겨가면 됩니다.
          </p>
          <button type="button" className="ed-btn ed-btn--spark ed-btn--lg" onClick={onStart}>
            무료로 시작하기 <ArrowRight size={18} />
          </button>
        </div>
      </section>

      {/* 푸터 */}
      <footer className="ed-wrap ed-foot">
        <div className="ed-foot-grid">
          <Wordmark />
          <p className="ed-fine">
            본 화면은 디자인 프로토타입입니다. HQA는 투자 자문이 아니며, 모든
            투자 판단과 손실의 책임은 투자자 본인에게 있습니다. 과거 수익률이
            미래의 수익을 보장하지 않습니다.
          </p>
        </div>
        <p className="ed-fine" style={{ marginTop: 20 }}>
          © 2026 HQA — 곁에서 돌보는 자동매매
        </p>
      </footer>
    </div>
  );
}

/* ============================================================
   8. 온보딩
   ============================================================ */
const LOSS_OPTS = [
  { id: "3", t: "-3%까지만", s: "작은 흔들림에도 얼른 멈춰요" },
  { id: "5", t: "-5%까지", s: "대부분의 분께 알맞아요" },
  { id: "10", t: "-10%까지", s: "변동을 견디고 기회를 노려요" },
];
const AMOUNTS = [300_000, 500_000, 1_000_000, 3_000_000];
const OB_N = 4;

function Onboarding({ onExit, onDone }: { onExit: () => void; onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [strategy, setStrategy] = useState<StrategyId>("balanced");
  const [amount, setAmount] = useState(500_000);
  const [loss, setLoss] = useState("5");
  const [broker, setBroker] = useState(false);
  const back = () => (step === 0 ? onExit() : setStep((s) => s - 1));

  return (
    <div className="ed-fade">
      <nav className="ed-nav">
        <div className="ed-nav-in">
          <Wordmark />
        </div>
      </nav>

      <div className="ed-wrap">
        <div className="ed-ob">
          {step === OB_N ? (
            <div>
              <span className="ed-label">준비 완료</span>
              <h2 className="ed-ob-q" style={{ marginTop: 14 }}>
                다 됐어요. 이제 곁에서 함께할게요.
              </h2>
              <div className="ed-ob-sum">
                {[
                  ["투자 성향", strategies.find((s) => s.id === strategy)?.name ?? ""],
                  ["월 투자 금액", won(amount)],
                  ["하루 손실 한도", `-${loss}%`],
                  ["연결 계좌", broker ? "한국투자증권" : "건너뜀"],
                ].map(([k, v]) => (
                  <div className="ed-ob-sum-row" key={k}>
                    <span>{k}</span>
                    <span>{v}</span>
                  </div>
                ))}
              </div>
              <div
                style={{
                  display: "flex",
                  gap: 14,
                  alignItems: "center",
                  borderTop: "1px solid var(--rule)",
                  paddingTop: 20,
                  marginBottom: 22,
                }}
              >
                <Portrait src={heroFace.photo} name={heroFace.name} size={52} />
                <div>
                  <p className="ed-serif" style={{ margin: 0, fontStyle: "italic", lineHeight: 1.5 }}>
                    “{heroFace.quote}”
                  </p>
                  <div style={{ fontSize: ".8rem", color: "var(--ink-3)", fontWeight: 700, marginTop: 5 }}>
                    — {heroFace.name} · {heroFace.role}
                  </div>
                </div>
              </div>
              <button type="button" className="ed-btn ed-btn--moss ed-btn--lg ed-btn--block" onClick={onDone}>
                운용 화면으로 <ArrowRight size={18} />
              </button>
            </div>
          ) : (
            <>
              <div className="ed-ob-top">
                <button type="button" className="ed-iconbtn" onClick={back} aria-label="뒤로">
                  <ArrowLeft size={17} />
                </button>
                <span className="ed-ob-count">
                  0{step + 1}
                  <em> / 0{OB_N}</em>
                </span>
              </div>
              <div className="ed-ob-track">
                <div className="ed-ob-fill" style={{ width: `${((step + 1) / OB_N) * 100}%` }} />
              </div>

              {step === 0 && (
                <div>
                  <span className="ed-label">투자 성향</span>
                  <h2 className="ed-ob-q" style={{ marginTop: 10 }}>
                    {user.name}님, 어떤 마음으로 투자하고 싶으세요?
                  </h2>
                  <p className="ed-ob-hint">고른 마음에 맞춰 종목과 비중을 정해드립니다. 언제든 바꿔도 괜찮아요.</p>
                  <div className="ed-ob-opts">
                    {strategies.map((s) => (
                      <button
                        type="button"
                        key={s.id}
                        className={`ed-ob-opt${strategy === s.id ? " ed-ob-opt--on" : ""}`}
                        onClick={() => setStrategy(s.id)}
                      >
                        <span className="ed-ob-opt-no">{s.no}</span>
                        <span className="ed-ob-opt-body">
                          <span className="ed-ob-opt-t">{s.name}</span>
                          <span className="ed-ob-opt-s">
                            목표 연 {s.targetReturn} · {s.tagline}
                          </span>
                        </span>
                        <span className="ed-ob-mark">
                          <CheckIcon size={15} strokeWidth={2.6} />
                        </span>
                      </button>
                    ))}
                  </div>
                  <p
                    className="ed-fine"
                    style={{ borderTop: "1px solid var(--rule)", paddingTop: 14, fontSize: ".86rem", color: "var(--ink-2)", lineHeight: 1.65 }}
                  >
                    <b style={{ fontWeight: 800 }}>{strategies.find((s) => s.id === strategy)?.name}</b>
                    은 지난 10년 백테스트에서 누적{" "}
                    <b className="ed-up ed-serif" style={{ fontSize: "1.06rem" }}>{BT[strategy].total}</b>
                    , 최대 낙폭{" "}
                    <b className="ed-down ed-serif" style={{ fontSize: "1.06rem" }}>{BT[strategy].mdd}</b>
                    을 기록했어요.
                  </p>
                </div>
              )}

              {step === 1 && (
                <div>
                  <span className="ed-label">투자 금액</span>
                  <h2 className="ed-ob-q" style={{ marginTop: 10 }}>매달 얼마를 함께 굴려볼까요?</h2>
                  <p className="ed-ob-hint">이 금액 안에서만 움직입니다. 부담 없는 만큼만 정해주세요.</p>
                  <div style={{ marginTop: 26, display: "flex", alignItems: "baseline", gap: 8 }}>
                    <input
                      className="ed-ob-input"
                      inputMode="numeric"
                      value={amount ? amount.toLocaleString("ko-KR") : ""}
                      onChange={(e) => {
                        const n = Number(e.target.value.replace(/[^0-9]/g, ""));
                        setAmount(Number.isNaN(n) ? 0 : n);
                      }}
                      style={{ flex: 1 }}
                      placeholder="0"
                    />
                    <span className="ed-serif" style={{ fontSize: "1.6rem", fontWeight: 700, color: "var(--ink-3)" }}>
                      원
                    </span>
                  </div>
                  <div className="ed-ob-chips">
                    {AMOUNTS.map((v) => (
                      <button
                        type="button"
                        key={v}
                        className={`ed-ob-chip${amount === v ? " ed-ob-chip--on" : ""}`}
                        onClick={() => setAmount(v)}
                      >
                        {v >= 1_000_000 ? `${v / 1_000_000}백만` : `${v / 10_000}만`}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 2 && (
                <div>
                  <span className="ed-label">위험 관리</span>
                  <h2 className="ed-ob-q" style={{ marginTop: 10 }}>하루 손실이 어디까지면 잠시 멈출까요?</h2>
                  <p className="ed-ob-hint">이 선에 닿으면 그날은 쉬어가며 강록님의 자산을 지킵니다.</p>
                  <div className="ed-ob-opts">
                    {LOSS_OPTS.map((o, i) => (
                      <button
                        type="button"
                        key={o.id}
                        className={`ed-ob-opt${loss === o.id ? " ed-ob-opt--on" : ""}`}
                        onClick={() => setLoss(o.id)}
                      >
                        <span className="ed-ob-opt-no">0{i + 1}</span>
                        <span className="ed-ob-opt-body">
                          <span className="ed-ob-opt-t">{o.t}</span>
                          <span className="ed-ob-opt-s">{o.s}</span>
                        </span>
                        <span className="ed-ob-mark">
                          <CheckIcon size={15} strokeWidth={2.6} />
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 3 && (
                <div>
                  <span className="ed-label">계좌 연결</span>
                  <h2 className="ed-ob-q" style={{ marginTop: 10 }}>마지막으로, 증권 계좌를 연결해요.</h2>
                  <p className="ed-ob-hint">
                    한국투자증권(KIS)과 안전하게 연결됩니다. 매매 권한만 쓰고
                    출금은 할 수 없으니 안심하세요.
                  </p>
                  <div style={{ marginTop: 26, paddingTop: 18, borderTop: "1px solid var(--rule)", borderBottom: "1px solid var(--rule)", paddingBottom: 18 }}>
                    <div className="ed-broker">
                      <span className="ed-broker-mk">KIS</span>
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: "block", fontWeight: 800 }}>한국투자증권</span>
                        <span style={{ display: "block", fontSize: ".82rem", color: "var(--ink-3)" }}>
                          {broker ? "계좌 ●●●●-●●12 연결됨" : "Open API 연동"}
                        </span>
                      </span>
                      {broker && (
                        <span className="ed-up" style={{ color: "var(--moss)" }}>
                          <CheckIcon size={20} strokeWidth={2.4} />
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    className={`ed-btn ed-btn--block ${broker ? "ed-btn--line" : "ed-btn--ink"}`}
                    style={{ marginTop: 16 }}
                    onClick={() => setBroker((v) => !v)}
                  >
                    {broker ? "연결 해제" : "한국투자증권 연결하기"}
                  </button>
                  <p className="ed-fine" style={{ marginTop: 12 }}>나중에 설정에서 연결해도 괜찮아요.</p>
                </div>
              )}

              <button
                type="button"
                className="ed-btn ed-btn--moss ed-btn--lg ed-btn--block"
                style={{ marginTop: 30 }}
                onClick={() => setStep((s) => s + 1)}
              >
                {step === OB_N - 1 ? "다 정했어요" : "다음"} <ArrowRight size={18} />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ============================================================
   9. 운용 화면
   ============================================================ */
function RunScreen({
  botRunning,
  setBotRunning,
  strategyId,
  onTab,
}: {
  botRunning: boolean;
  setBotRunning: (v: boolean) => void;
  strategyId: StrategyId;
  onTab: (t: AppTab) => void;
}) {
  const strategy = strategies.find((s) => s.id === strategyId)!;
  return (
    <div className="ed-wrap ed-app ed-fade" style={{ maxWidth: 920 }}>
      <div className="ed-app-head">
        <div>
          <div className="ed-kicker">2026년 5월 22일 금요일</div>
          <h1 className="ed-app-h">좋은 오후예요, {user.name}님.</h1>
        </div>
      </div>

      <p
        className="ed-quote"
        style={{ marginTop: 16, maxWidth: "26em", fontSize: "clamp(1.1rem,1.9vw,1.42rem)" }}
      >
        오늘은 반도체가 강했어요. 강록님 자산은 차분히 <em>+1.28%</em> 늘었습니다.
      </p>

      {/* 운용 밴드 */}
      <section className={`ed-runband ${botRunning ? "ed-runband--on" : "ed-runband--off"}`}>
        <div className="ed-runband-main">
          <span className="ed-eyebrow" style={botRunning ? { color: "var(--spark)" } : undefined}>
            <span className={`ed-dot${botRunning ? " ed-dot--live" : ""}`} />
            {botRunning ? "운용 중" : "정지됨"}
          </span>
          <div className="ed-runband-h">
            {botRunning ? "AI가 자산을 돌보고 있어요" : "AI가 잠시 쉬고 있어요"}
          </div>
          <div className="ed-runband-sub">
            {botRunning
              ? `${strategy.name} 전략 · 한국투자증권 모의투자`
              : "준비되면 깨워주세요. 강록님의 전략대로 다시 시작할게요."}
          </div>
        </div>
        {botRunning && (
          <div className="ed-runband-stats">
            <div className="ed-runstat">
              <small>오늘 체결</small>
              <b>8건</b>
            </div>
            <div className="ed-runstat">
              <small>가동 시간</small>
              <b>6h 12m</b>
            </div>
            <div className="ed-runstat">
              <small>대기 신호</small>
              <b>3건</b>
            </div>
          </div>
        )}
        <button
          type="button"
          className={`ed-runbtn ${botRunning ? "ed-runbtn--stop" : "ed-runbtn--start"}`}
          onClick={() => setBotRunning(!botRunning)}
        >
          {botRunning ? (
            <>
              <PauseIcon size={17} /> 잠시 멈추기
            </>
          ) : (
            <>
              <PlayIcon size={17} /> 자동매매 시작
            </>
          )}
        </button>
      </section>

      {/* 자산 — 큰 숫자, 카드 없음 */}
      <div style={{ marginTop: 8 }}>
        <span className="ed-eyebrow">내 총자산</span>
        <div className="ed-figrow">
          <div className="ed-fig ed-fig--xl">
            <b>{won(account.totalValue)}</b>
            <div className={`ed-fig-delta ${tone(account.dayChange)}`}>
              ▲ {signedWon(account.dayChange)} ({signedPct(account.dayChangePct)}) 오늘
            </div>
          </div>
        </div>
        <MiniChart data={equityCurve} />
        <div className="ed-figrow" style={{ marginTop: 18 }}>
          <div className="ed-fig ed-fig--md">
            <small>오늘 실현 손익</small>
            <b className={tone(account.realizedToday)}>{signedWon(account.realizedToday)}</b>
          </div>
          <div className="ed-fig ed-fig--md">
            <small>평가 손익</small>
            <b className={tone(account.unrealized)}>{signedWon(account.unrealized)}</b>
          </div>
          <div className="ed-fig ed-fig--md">
            <small>예수금</small>
            <b>{won(account.cash)}</b>
          </div>
        </div>
      </div>

      {/* AI 노트 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">AI의 메모</span>
          <span className="ed-eyebrow">
            <span className="ed-dot ed-dot--live" /> 실시간
          </span>
        </div>
        {signals.map((s, i) => (
          <div className="ed-note" key={i}>
            <span className="ed-note-tick">AI</span>
            <div>
              <p dangerouslySetInnerHTML={{ __html: s.text }} />
              <time>{s.time}</time>
            </div>
          </div>
        ))}
      </section>

      {/* 보유 종목 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">함께하는 종목</span>
          <span className="ed-eyebrow">{positions.length}개 종목</span>
        </div>
        {positions.map((p, i) => {
          const { value, pl, pct } = positionPL(p);
          return (
            <div className="ed-row" key={p.code}>
              <span className="ed-row-no">{String(i + 1).padStart(2, "0")}</span>
              <div className="ed-row-main">
                <div className="ed-row-name">{p.name}</div>
                <div className="ed-row-meta">
                  {p.shares}주 · 평균 {p.avgPrice.toLocaleString("ko-KR")}원
                </div>
              </div>
              <div className="ed-row-num">
                <div className="ed-row-val">{won(value)}</div>
                <div className={`ed-row-pl ${tone(pl)}`}>{signedPct(pct)}</div>
              </div>
            </div>
          );
        })}
      </section>

      {/* 현재 전략 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">지금의 전략</span>
          <button type="button" className="ed-sec-link" onClick={() => onTab("strategy")}>
            바꾸기 →
          </button>
        </div>
        <div className="ed-row" style={{ borderBottom: 0 }}>
          <span className="ed-row-no" style={{ color: "var(--moss)" }}>
            {strategy.no}
          </span>
          <div className="ed-row-main">
            <div className="ed-row-name">{strategy.name}</div>
            <div className="ed-row-meta">{strategy.tagline}</div>
          </div>
          <div className="ed-row-num">
            <div className="ed-row-val">연 {strategy.targetReturn}</div>
            <div className="ed-row-pl" style={{ color: "var(--ink-3)" }}>
              목표 수익률
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

/* ============================================================
   10. 전략 화면
   ============================================================ */
const RISK_TEXT: Record<1 | 2 | 3, string> = { 1: "낮음", 2: "보통", 3: "높음" };

function StrategyScreen({
  strategyId,
  setStrategyId,
  botRunning,
}: {
  strategyId: StrategyId;
  setStrategyId: (id: StrategyId) => void;
  botRunning: boolean;
}) {
  const [pending, setPending] = useState<StrategyId>(strategyId);
  const applied = pending === strategyId;
  return (
    <div className="ed-wrap ed-app ed-fade">
      <div className="ed-kicker">전략</div>
      <h1 className="ed-app-h">어떤 마음으로 함께할까요?</h1>
      <p className="ed-lede" style={{ marginTop: 10, maxWidth: "34em" }}>
        고른 전략대로 AI가 종목과 비중을 정합니다. 목표가 높을수록 마음의
        준비도 조금 더 필요해요.
      </p>

      <hr className="ed-rule" style={{ margin: "26px 0 0" }} />
      <div className="ed-strat-cols">
        {strategies.map((s) => {
          const on = pending === s.id;
          return (
            <div
              key={s.id}
              role="button"
              tabIndex={0}
              className={`ed-strat-col${on ? " ed-strat-col--on" : ""}`}
              onClick={() => setPending(s.id)}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setPending(s.id)}
            >
              <div className="ed-strat-no">{s.no}</div>
              <div className="ed-strat-name">{s.name}</div>
              <div className="ed-strat-tag">{s.tagline}</div>
              <p className="ed-strat-desc">{s.description}</p>
              <div className="ed-strat-m">
                <span>목표 수익률</span>
                <b>연 {s.targetReturn}</b>
              </div>
              <div className="ed-strat-m">
                <span>최대 낙폭</span>
                <b>{s.mdd}</b>
              </div>
              <div className="ed-strat-m">
                <span>거래 빈도</span>
                <b>{s.frequency}</b>
              </div>
              <div className="ed-strat-m" style={{ borderBottom: 0 }}>
                <span>위험도</span>
                <b>{RISK_TEXT[s.riskLevel]}</b>
              </div>
              <div className="ed-strat-pick">
                {on ? (
                  <>
                    <CheckIcon size={16} strokeWidth={2.6} /> 선택됨
                  </>
                ) : (
                  "선택하기"
                )}
              </div>
            </div>
          );
        })}
      </div>
      <hr className="ed-rule" />

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20, flexWrap: "wrap", marginTop: 24 }}>
        <p className="ed-fine" style={{ flex: 1, minWidth: 220 }}>
          {botRunning
            ? "운용 중에 바꾸면 다음 매매부터 새 전략으로 함께합니다."
            : "전략을 정한 뒤 운용 화면에서 자동매매를 시작하세요."}
        </p>
        <button
          type="button"
          className="ed-btn ed-btn--moss ed-btn--lg"
          disabled={applied}
          onClick={() => setStrategyId(pending)}
        >
          {applied ? "지금 이 전략입니다" : `${strategies.find((s) => s.id === pending)?.name}으로 바꾸기`}
        </button>
      </div>
    </div>
  );
}

/* ============================================================
   11. 거래 기록
   ============================================================ */
type Filter = "all" | "buy" | "sell";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "전체" },
  { id: "buy", label: "매수" },
  { id: "sell", label: "매도" },
];

function HistoryScreen() {
  const [filter, setFilter] = useState<Filter>("all");
  const rows = trades.filter((t) => filter === "all" || t.side === filter);
  const dates = Array.from(new Set(rows.map((t) => t.date)));
  return (
    <div className="ed-wrap ed-app ed-fade" style={{ maxWidth: 920 }}>
      <div className="ed-kicker">기록</div>
      <h1 className="ed-app-h">함께 걸어온 기록.</h1>

      <div className="ed-figrow" style={{ marginTop: 24 }}>
        <div className="ed-fig ed-fig--md">
          <small>이번 달 실현 손익</small>
          <b className={tone(monthly.realized)}>{signedWon(monthly.realized)}</b>
        </div>
        <div className="ed-fig ed-fig--md">
          <small>거래 횟수</small>
          <b>{monthly.count}회</b>
        </div>
        <div className="ed-fig ed-fig--md">
          <small>승률</small>
          <b>{monthly.winRate}%</b>
        </div>
        <div className="ed-fig ed-fig--md">
          <small>AI와 함께한 지</small>
          <b>{monthly.days}일</b>
        </div>
      </div>

      <div className="ed-filters" style={{ marginTop: 30 }}>
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            className={`ed-filter${filter === f.id ? " ed-filter--on" : ""}`}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {dates.map((d) => (
        <div key={d}>
          <div className="ed-daterow">{d}</div>
          {rows
            .filter((t) => t.date === d)
            .map((t) => (
              <div className="ed-trade" key={t.id}>
                <span className={`ed-trade-side ed-trade-side--${t.side}`}>
                  {t.side === "buy" ? "매수" : "매도"}
                </span>
                <div className="ed-row-main">
                  <div className="ed-row-name">{t.name}</div>
                  <div className="ed-row-meta">
                    {t.shares}주 · {t.price.toLocaleString("ko-KR")}원 · {t.time}
                  </div>
                </div>
                <div className="ed-row-num">
                  <div className="ed-row-val">{won(t.shares * t.price)}</div>
                  {t.side === "sell" && t.realizedPL !== undefined && (
                    <div className={`ed-row-pl ${tone(t.realizedPL)}`}>{signedWon(t.realizedPL)}</div>
                  )}
                </div>
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}

/* ============================================================
   12. 설정
   ============================================================ */
function SettingsScreen({
  theme,
  setTheme,
  strategyId,
  onLogout,
}: {
  theme: Theme;
  setTheme: (t: Theme) => void;
  strategyId: StrategyId;
  onLogout: () => void;
}) {
  const [env, setEnv] = useState<"sim" | "real">("sim");
  const [alerts, setAlerts] = useState({ fill: true, report: true, loss: true });
  const strategy = strategies.find((s) => s.id === strategyId)!;
  return (
    <div className="ed-wrap ed-app ed-fade" style={{ maxWidth: 760 }}>
      <div className="ed-kicker">설정</div>
      <h1 className="ed-app-h">계정과 운용 설정.</h1>

      {/* 프로필 */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 26, paddingBottom: 24, borderBottom: "1.5px solid var(--ink)" }}>
        <Portrait src={user.photo} name={user.name} size={58} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 800, fontSize: "1.12rem" }}>{user.name}님</div>
          <div style={{ fontSize: ".84rem", color: "var(--ink-3)" }}>{user.email}</div>
        </div>
        <span className="ed-serif" style={{ fontStyle: "italic", color: "var(--ink-3)" }}>
          {strategy.name}
        </span>
      </div>

      {/* 증권 계좌 */}
      <section className="ed-sec" style={{ marginTop: 36 }}>
        <div className="ed-sec-head">
          <span className="ed-sec-title">증권 계좌</span>
        </div>
        <div className="ed-broker" style={{ padding: "16px 2px" }}>
          <span className="ed-broker-mk">KIS</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 800 }}>한국투자증권</div>
            <div style={{ fontSize: ".82rem", color: "var(--ink-3)" }}>계좌 ●●●●-●●12</div>
          </div>
          <span className="ed-eyebrow" style={{ color: "var(--moss)" }}>
            <CheckIcon size={14} strokeWidth={3} /> 연결됨
          </span>
        </div>
        <div className="ed-set-row ed-set-row--static">
          <div className="ed-set-body">
            <div className="ed-set-label">투자 환경</div>
            <div className="ed-set-desc">충분히 연습한 뒤 실전으로 옮겨가세요</div>
          </div>
          <div className="ed-seg">
            <button type="button" className={`ed-seg-btn${env === "sim" ? " ed-seg-btn--on" : ""}`} onClick={() => setEnv("sim")}>
              모의투자
            </button>
            <button type="button" className={`ed-seg-btn${env === "real" ? " ed-seg-btn--on" : ""}`} onClick={() => setEnv("real")}>
              실전투자
            </button>
          </div>
        </div>
      </section>

      {/* 자동매매 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">자동매매</span>
        </div>
        {[
          { Ico: WalletIcon, label: "월 투자 한도", desc: "이 금액 안에서만 함께해요", val: won(500_000) },
          { Ico: SlidersIcon, label: "한 종목 최대 비중", desc: "한곳에 쏠리지 않도록", val: "20%" },
          { Ico: ShieldIcon, label: "하루 손실 한도", desc: "여기 닿으면 잠시 쉬어가요", val: "-5%" },
          { Ico: SparkIcon, label: "투자 전략", desc: "지금의 운용 방식", val: strategy.name },
        ].map(({ Ico, label, desc, val }) => (
          <button type="button" className="ed-set-row" key={label}>
            <Ico size={20} />
            <span className="ed-set-body">
              <span className="ed-set-label">{label}</span>
              <span className="ed-set-desc">{desc}</span>
            </span>
            <span className="ed-set-val">{val}</span>
            <ArrowRight size={16} />
          </button>
        ))}
      </section>

      {/* 알림 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">알림</span>
        </div>
        {[
          { key: "fill" as const, Ico: BellIcon, label: "체결 알림", desc: "사고팔 때 살짝 알려드려요" },
          { key: "report" as const, Ico: FileIcon, label: "하루 마무리 메모", desc: "장 마감 후 오늘의 이야기" },
          { key: "loss" as const, Ico: ShieldIcon, label: "손실 다독임", desc: "손실선에 가까워지면 미리" },
        ].map(({ key, Ico, label, desc }) => (
          <div className="ed-set-row ed-set-row--static" key={key}>
            <Ico size={20} />
            <span className="ed-set-body">
              <span className="ed-set-label">{label}</span>
              <span className="ed-set-desc">{desc}</span>
            </span>
            <Switch on={alerts[key]} onClick={() => setAlerts((a) => ({ ...a, [key]: !a[key] }))} />
          </div>
        ))}
      </section>

      {/* 화면 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">화면</span>
        </div>
        <div className="ed-set-row ed-set-row--static">
          <div className="ed-set-body">
            <div className="ed-set-label">테마</div>
            <div className="ed-set-desc">눈이 편한 쪽으로 골라주세요</div>
          </div>
          <div className="ed-seg">
            <button type="button" className={`ed-seg-btn${theme === "light" ? " ed-seg-btn--on" : ""}`} onClick={() => setTheme("light")}>
              낮
            </button>
            <button type="button" className={`ed-seg-btn${theme === "dark" ? " ed-seg-btn--on" : ""}`} onClick={() => setTheme("dark")}>
              밤
            </button>
          </div>
        </div>
      </section>

      {/* 계정 */}
      <section className="ed-sec">
        <div className="ed-sec-head">
          <span className="ed-sec-title">계정</span>
        </div>
        <button type="button" className="ed-set-row">
          <FileIcon size={20} />
          <span className="ed-set-body">
            <span className="ed-set-label">투자 위험 안내</span>
            <span className="ed-set-desc">함께하기 전 꼭 알아둘 이야기</span>
          </span>
          <ArrowRight size={16} />
        </button>
        <button type="button" className="ed-set-row" onClick={onLogout}>
          <LogoutIcon size={20} />
          <span className="ed-set-body">
            <span className="ed-set-label">로그아웃</span>
          </span>
        </button>
      </section>

      <p className="ed-fine" style={{ marginTop: 30 }}>
        HQA 자동매매 · 디자인 프로토타입 v0.4 · 투자 손실은 투자자 본인에게
        귀속됩니다.
      </p>
    </div>
  );
}

/* ============================================================
   13. 루트
   ============================================================ */
export default function HQAEditorialPrototype() {
  const [theme, setTheme] = useState<Theme>("light");
  const [route, setRoute] = useState<Route>("landing");
  const [tab, setTab] = useState<AppTab>("run");
  const [botRunning, setBotRunning] = useState(true);
  const [strategyId, setStrategyId] = useState<StrategyId>("balanced");

  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = window.localStorage.getItem("ed-theme");
    if (saved === "light" || saved === "dark") setTheme(saved);
  }, []);

  const applyTheme = (t: Theme) => {
    setTheme(t);
    if (typeof window !== "undefined") window.localStorage.setItem("ed-theme", t);
  };
  const toggleTheme = () => applyTheme(theme === "dark" ? "light" : "dark");

  return (
    <div className="ed" data-theme={theme} suppressHydrationWarning>
      <style dangerouslySetInnerHTML={{ __html: CSS }} />

      {route === "landing" && (
        <Landing
          theme={theme}
          onToggleTheme={toggleTheme}
          onStart={() => setRoute("onboarding")}
          onLogin={() => {
            setTab("run");
            setRoute("app");
          }}
        />
      )}

      {route === "onboarding" && (
        <Onboarding
          onExit={() => setRoute("landing")}
          onDone={() => {
            setTab("run");
            setRoute("app");
          }}
        />
      )}

      {route === "app" && (
        <>
          <AppNav
            tab={tab}
            onTab={setTab}
            theme={theme}
            onToggleTheme={toggleTheme}
            botRunning={botRunning}
            onToggleBot={() => setBotRunning(!botRunning)}
          />
          {tab === "run" && (
            <RunScreen botRunning={botRunning} setBotRunning={setBotRunning} strategyId={strategyId} onTab={setTab} />
          )}
          {tab === "strategy" && (
            <StrategyScreen strategyId={strategyId} setStrategyId={setStrategyId} botRunning={botRunning} />
          )}
          {tab === "history" && <HistoryScreen />}
          {tab === "settings" && (
            <SettingsScreen theme={theme} setTheme={applyTheme} strategyId={strategyId} onLogout={() => setRoute("landing")} />
          )}
        </>
      )}
    </div>
  );
}
