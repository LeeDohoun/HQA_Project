"use client";

/* ============================================================
   HQA — 로그인 전 제품 소개 홈페이지 (랜딩)
   에디토리얼 디자인 · 백테스트 결과 · 실제 인물 사진
   디자인은 /prototype 의 랜딩을 그대로 따름. CTA는 실제 라우트로 연결.
   ============================================================ */

import { useEffect, useMemo, useState } from "react";
import type { ReactNode, SVGProps } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";
import { loadAiBacktestComparison } from "@/lib/backtesting";
import type { BacktestComparisonBundle } from "@/types/backtesting";

/* ============================================================
   1. 디자인 시스템 (에디토리얼)
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
.ed-mark{display:inline-flex; align-items:baseline; gap:0;}
.ed-mark b{font-family:var(--serif); font-style:italic; font-weight:700; font-size:1.5rem; letter-spacing:-.02em;}
.ed-mark i{width:7px; height:7px; border-radius:50%; background:var(--moss); margin-left:3px; align-self:flex-end; margin-bottom:5px; font-style:normal;}
.ed-nav-right{margin-left:auto; display:flex; align-items:center; gap:10px;}
.ed-iconbtn{
  width:38px; height:38px; border:1px solid var(--rule); background:transparent; color:var(--ink-2);
  cursor:pointer; display:inline-flex; align-items:center; justify-content:center; border-radius:5px;
  transition:border-color .14s,color .14s;
}
.ed-iconbtn:hover{border-color:var(--ink); color:var(--ink);}

.ed-portrait{border-radius:50%; object-fit:cover; background:var(--paper-2);}
.ed-portrait-fb{
  border-radius:50%; display:inline-flex; align-items:center; justify-content:center;
  font-family:var(--serif); font-weight:700; color:#fff; flex-shrink:0;
}

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

.ed-trust{display:flex; align-items:center; gap:20px; flex-wrap:wrap; padding:26px 0;}
.ed-faces{display:flex;}
.ed-faces > *{margin-left:-12px; box-shadow:0 0 0 3px var(--paper);}
.ed-faces > *:first-child{margin-left:0;}
.ed-trust-txt b{font-family:var(--serif); font-size:1.3rem; font-weight:700;}
.ed-trust-txt span{color:var(--ink-2); font-size:.92rem;}

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

.ed-step{
  display:grid; grid-template-columns:auto 1fr; gap:clamp(20px,4vw,48px); align-items:start;
  padding:clamp(28px,4vw,44px) 0; border-bottom:1px solid var(--rule);
}
.ed-step:last-child{border-bottom:0;}
.ed-step-no{font-family:var(--serif); font-size:clamp(3rem,7vw,5.4rem); font-weight:700; line-height:.85; color:var(--moss);}
.ed-step-h{font-size:clamp(1.3rem,2.4vw,1.8rem); font-weight:800; letter-spacing:-.025em; margin:0;}
.ed-step-p{margin:10px 0 0; color:var(--ink-2); max-width:34em; line-height:1.65;}

.ed-feature-q{display:grid; grid-template-columns:auto 1fr; gap:clamp(20px,4vw,40px); align-items:center;}
.ed-q-meta{display:flex; align-items:center; gap:13px; margin-top:22px;}
.ed-q-name{font-weight:800;}
.ed-q-role{font-size:.84rem; color:var(--ink-3);}
.ed-q-row{display:grid; grid-template-columns:1fr 1fr; gap:clamp(20px,4vw,44px); margin-top:8px;}
.ed-q-small p{font-family:var(--serif); font-size:1.12rem; line-height:1.5; margin:0;}

.ed-finale{text-align:center;}
.ed-finale .ed-h2{max-width:16em; margin:0 auto;}

.ed-foot{border-top:1px solid var(--rule); padding:40px 0;}
.ed-foot-grid{display:flex; justify-content:space-between; gap:24px; flex-wrap:wrap; align-items:flex-start;}
.ed-fine{font-size:.78rem; color:var(--ink-3); line-height:1.7; max-width:42em;}

.ed-fade{animation:ed-fade .5s var(--ease) both;}
@keyframes ed-fade{from{opacity:0; transform:translateY(14px);}to{opacity:1; transform:translateY(0);}}

@media (max-width:920px){
  .ed-hero-grid{grid-template-columns:1fr; gap:38px;}
  .ed-collage{min-height:330px; max-width:420px;}
  .ed-bt-stage{grid-template-columns:1fr; gap:30px;}
  .ed-feature-q{grid-template-columns:1fr; gap:8px;}
  .ed-q-row{grid-template-columns:1fr; gap:22px;}
}
@media (max-width:680px){
  .ed-nav-in{padding:0 16px; gap:6px;}
  .ed-step{grid-template-columns:1fr; gap:6px;}
  .ed-bt-tab{margin-right:18px;}
}
@media (prefers-reduced-motion:reduce){
  .ed *{animation-duration:.001ms !important; transition-duration:.001ms !important;}
}
`;

/* ============================================================
   2. 아이콘
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

/* ============================================================
   3. 타입 · 데이터
   ============================================================ */
type Theme = "light" | "dark";

interface Person {
  name: string;
  role: string;
  quote: string;
  photo: string;
}
const featured: Person = {
  name: "이강록",
  role: "38세 · 콘텐츠 마케터",
  quote: "퇴근하고 차트만 들여다보던 시간이 사라졌어요. 그 시간에 이제 아이와 저녁을 먹습니다.",
  photo: "",
};
const reviews: Person[] = [
  {
    name: "하제학",
    role: "45세 · 자영업",
    quote: "감정적으로 사고팔던 버릇이 없어졌습니다. 규칙대로 움직이니 마음이 놓여요.",
    photo: "",
  },
  {
    name: "이도훈",
    role: "31세 · 소프트웨어 개발자",
    quote: "백테스트를 연도별로 직접 확인하고 나서야 믿음이 생겼어요. 숫자가 솔직하더라고요.",
    photo: "",
  },
];
const heroFace: Person = {
  name: "이호준",
  role: "균형형 운용 · 7개월째",
  quote: "주말엔 휴대폰을 안 봐요. 그래도 잘 굴러갑니다.",
  photo: "",
};
// 신뢰 스트립의 작은 아바타들 — 이니셜로. 한 글자씩 골라 다양한 색상이 나오게.
const trustFaces: { name: string; photo: string }[] = [
  { name: "김", photo: "" },
  { name: "박", photo: "" },
  { name: "최", photo: "" },
  { name: "정", photo: "" },
  { name: "강", photo: "" },
  { name: "윤", photo: "" },
];

/* ============================================================
   4. 인물 사진 — 로드 실패 시 이니셜로 폴백
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
  // 빈 src는 이니셜 아바타로 곧장 — 의도된 폴백.
  if (failed || !src) {
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
   5. 백테스트 막대 차트 — 연도별 × short/long, center 전략 수익률
   ============================================================ */
type BtBar = { period: string; horizon: "short" | "long"; value: number; mdd: number };

function BacktestBars({ bars }: { bars: BtBar[] }) {
  // bars는 period asc, period 안에서 short → long 순.
  // 0을 기준으로 위/아래로 막대를 그림. 음수도 자연스럽게.
  const w = 720;
  const h = 280;
  const padL = 36;
  const padR = 12;
  const padT = 18;
  const padB = 46;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;

  const periods = Array.from(new Set(bars.map((b) => b.period)));
  const groupW = innerW / Math.max(periods.length, 1);
  const barW = Math.min(28, (groupW - 12) / 2);

  const values = bars.map((b) => b.value);
  const rawMax = Math.max(0, ...values);
  const rawMin = Math.min(0, ...values);
  const span = Math.max(Math.abs(rawMax), Math.abs(rawMin), 10);
  const yMax = span;
  const yMin = -span;
  const yRange = yMax - yMin;
  const zeroY = padT + (yMax / yRange) * innerH;

  function barHeight(v: number) {
    return (Math.abs(v) / yRange) * innerH;
  }

  return (
    <svg className="ed-chart" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="연도별 백테스트 수익률">
      {/* 기준선 (0%) */}
      <line x1={padL} x2={w - padR} y1={zeroY} y2={zeroY} stroke="var(--ink-2)" strokeWidth={1} />
      {/* 가로 보조선 */}
      {[0.25, 0.5, 0.75].map((p) => (
        <line
          key={p}
          x1={padL}
          x2={w - padR}
          y1={padT + p * innerH}
          y2={padT + p * innerH}
          stroke="var(--rule)"
          strokeWidth={1}
          strokeDasharray="3 4"
        />
      ))}
      {/* Y축 라벨 */}
      <text x={padL - 6} y={padT + 4} textAnchor="end" fontSize="10" fill="var(--ink-3)">
        +{yMax.toFixed(0)}%
      </text>
      <text x={padL - 6} y={zeroY + 4} textAnchor="end" fontSize="10" fill="var(--ink-3)">
        0%
      </text>
      <text x={padL - 6} y={padT + innerH + 4} textAnchor="end" fontSize="10" fill="var(--ink-3)">
        {yMin.toFixed(0)}%
      </text>

      {/* 막대 + 라벨 */}
      {periods.map((period, gi) => {
        const groupX = padL + groupW * gi + groupW / 2;
        const periodBars = bars.filter((b) => b.period === period);
        return (
          <g key={period}>
            {periodBars.map((b, bi) => {
              const offset = (bi - (periodBars.length - 1) / 2) * (barW + 6);
              const x = groupX + offset - barW / 2;
              const isShort = b.horizon === "short";
              const fill = isShort ? "var(--moss)" : "var(--spark)";
              const y = b.value >= 0 ? zeroY - barHeight(b.value) : zeroY;
              return (
                <g key={b.horizon}>
                  <rect
                    x={x}
                    y={y}
                    width={barW}
                    height={barHeight(b.value)}
                    fill={fill}
                    opacity={0.92}
                    rx={2}
                  />
                  <text
                    x={x + barW / 2}
                    y={b.value >= 0 ? y - 4 : y + barHeight(b.value) + 12}
                    textAnchor="middle"
                    fontSize="10"
                    fontWeight={800}
                    fill={b.value >= 0 ? "var(--up)" : "var(--down)"}
                  >
                    {b.value >= 0 ? "+" : ""}{b.value.toFixed(1)}%
                  </text>
                </g>
              );
            })}
            <text
              x={groupX}
              y={h - 22}
              textAnchor="middle"
              fontSize="11"
              fontWeight={800}
              fill="var(--ink-2)"
            >
              {period}
            </text>
          </g>
        );
      })}

      {/* 범례 */}
      <g>
        <rect x={padL} y={h - 12} width={10} height={3} fill="var(--moss)" />
        <text x={padL + 14} y={h - 8} fontSize="10" fontWeight={700} fill="var(--ink-2)">
          단기 (5일 보유)
        </text>
        <rect x={padL + 88} y={h - 12} width={10} height={3} fill="var(--spark)" />
        <text x={padL + 102} y={h - 8} fontSize="10" fontWeight={700} fill="var(--ink-2)">
          장기 (20일 보유)
        </text>
      </g>
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
function Wordmark() {
  return (
    <span className="ed-mark">
      <b>HQA</b>
      <i />
    </span>
  );
}

/* ============================================================
   7. 랜딩 페이지
   ============================================================ */
export default function HomePage() {
  const router = useRouter();
  const [theme, setTheme] = useState<Theme>("light");
  const [authedTarget, setAuthedTarget] = useState<string | null>(null);
  const [comparison, setComparison] = useState<BacktestComparisonBundle | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem("hqa-theme");
    if (saved === "light" || saved === "dark") setTheme(saved);
  }, []);

  useEffect(() => {
    let active = true;
    authApi
      .me()
      .then((u) => {
        if (active) setAuthedTarget(u.surveyCompleted ? "/dashboard" : "/onboarding/preference");
      })
      .catch(() => {
        /* 비로그인 상태 — 정상 */
      });
    return () => {
      active = false;
    };
  }, []);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    window.localStorage.setItem("hqa-theme", next);
  };

  useEffect(() => {
    let active = true;
    loadAiBacktestComparison()
      .then((c) => { if (active) setComparison(c); })
      .catch(() => { /* 정적 자료 — 실패 시 섹션을 숨김 */ });
    return () => { active = false; };
  }, []);

  const goStart = () => router.push(authedTarget ?? "/signup");
  const goLogin = () => router.push(authedTarget ?? "/login");
  const goBacktestDetail = () => router.push("/backtesting/ai");

  // 백테스트 요약 — period 오름차순으로 정렬, short/long 모두.
  const btBars: BtBar[] = useMemo(() => {
    if (!comparison) return [];
    const sorted = [...comparison.summary].sort((a, b) => {
      if (a.period !== b.period) return a.period.localeCompare(b.period);
      return a.horizon === "short" ? -1 : 1;
    });
    return sorted.map((s) => ({
      period: s.period,
      horizon: s.horizon,
      value: s.center_return_pct,
      mdd: s.center_mdd_pct,
    }));
  }, [comparison]);

  // 집계 지표 — center 전략의 단순 평균(연도 가중). 화려한 보장 대신 정직한 평균.
  const btStats = useMemo(() => {
    if (!comparison || comparison.summary.length === 0) return null;
    const arr = comparison.summary;
    const avgReturn = arr.reduce((a, s) => a + s.center_return_pct, 0) / arr.length;
    const worstMdd = arr.reduce((min, s) => Math.min(min, s.center_mdd_pct), 0);
    const wins = comparison.rows.filter((r) => r.is_center_multi_agent && r.beats_center_return === false && r.return_delta_vs_center_multi_agent_pct < 0).length;
    const totalRows = comparison.rows.length;
    return {
      avgReturn,
      worstMdd,
      periods: Array.from(new Set(arr.map((s) => s.period))).length,
      totalRows,
      relWins: wins,
    };
  }, [comparison]);

  return (
    <div className="ed" data-theme={theme} suppressHydrationWarning>
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <div className="ed-fade">
        {/* 네비 */}
        <nav className="ed-nav">
          <div className="ed-nav-in">
            <Wordmark />
            <div className="ed-nav-right">
              <ThemeToggle theme={theme} onToggle={toggleTheme} />
              {authedTarget ? (
                <button type="button" className="ed-btn ed-btn--ink" onClick={() => router.push(authedTarget)}>
                  내 대시보드
                </button>
              ) : (
                <>
                  <button type="button" className="ed-tlink" style={{ fontSize: ".9rem" }} onClick={goLogin}>
                    로그인
                  </button>
                  <button type="button" className="ed-btn ed-btn--ink" onClick={goStart}>
                    시작하기
                  </button>
                </>
              )}
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
                HQA의 AI가 한국 증시를 24시간 지켜보며, 당신이 정한 전략대로
                직접 사고팝니다. 당신은 그저 일상을 살아가면 됩니다.
              </p>
              <div className="ed-hero-cta">
                <button type="button" className="ed-btn ed-btn--moss ed-btn--lg" onClick={goStart}>
                  3분 만에 시작하기 <ArrowRight size={18} />
                </button>
                <button type="button" className="ed-tlink" onClick={goLogin}>
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

            {/* 콜라주 */}
            <div className="ed-collage">
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
              {trustFaces.map((f, i) => (
                <Portrait key={i} src={f.photo} name={f.name} size={42} />
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

        {/* 백테스트 — 실제 AI 멀티에이전트 전략 결과 */}
        <section className="ed-wrap ed-band">
          <div className="ed-bt-head">
            <div>
              <span className="ed-label">
                백테스트 · AI 멀티에이전트 {comparison ? `· ${comparison.theme} 테마` : ""}
              </span>
              <h2 className="ed-h2" style={{ marginTop: 14 }}>
                숫자로 보여드립니다.
                <br />
                연도별 실측 결과.
              </h2>
            </div>
            <p className="ed-lede" style={{ maxWidth: "22em" }}>
              HQA의 multi-agent hybrid 전략을 실제 데이터로 재현했습니다.
              상승만 있는 그림이 아니라, 있는 그대로의 연도별 성과입니다.
            </p>
          </div>

          {comparison && btBars.length > 0 ? (
            <>
              <div className="ed-bt-stage">
                <div>
                  <BacktestBars bars={btBars} />
                </div>

                <div className="ed-bt-figs">
                  <div className="ed-bt-fig">
                    <span className="ed-bt-fig-label">평균 연 수익률</span>
                    <span
                      className={`ed-bt-fig-val ${(btStats?.avgReturn ?? 0) >= 0 ? "ed-up" : "ed-down"}`}
                    >
                      {btStats ? `${btStats.avgReturn >= 0 ? "+" : ""}${btStats.avgReturn.toFixed(1)}%` : "-"}
                    </span>
                  </div>
                  <div className="ed-bt-fig">
                    <span className="ed-bt-fig-label">검증 연도 수</span>
                    <span className="ed-bt-fig-val">{btStats?.periods ?? "-"}</span>
                  </div>
                  <div className="ed-bt-fig">
                    <span className="ed-bt-fig-label">최대 낙폭 (MDD)</span>
                    <span className="ed-bt-fig-val ed-down">
                      {btStats ? `${btStats.worstMdd.toFixed(1)}%` : "-"}
                    </span>
                  </div>
                  <div className="ed-bt-fig">
                    <span className="ed-bt-fig-label">비교 전략 수</span>
                    <span className="ed-bt-fig-val">{btStats?.totalRows ?? "-"}</span>
                  </div>
                </div>
              </div>

              <div style={{ display: "flex", gap: 14, alignItems: "center", marginTop: 26, flexWrap: "wrap" }}>
                <button type="button" className="ed-btn ed-btn--ink" onClick={goBacktestDetail}>
                  상세 백테스트 보기 <ArrowRight size={16} />
                </button>
                <span className="ed-fine">
                  RSI · 볼린저밴드 · 규칙기반과의 비교, 전체 행 단위 데이터까지 확인할 수 있어요.
                </span>
              </div>

              <p className="ed-fine" style={{ marginTop: 18 }}>
                백테스트 결과는 과거 데이터를 기반으로 한 시뮬레이션이며, 미래 수익을 보장하지 않습니다.
                실제 운용은 시장 상황, 슬리피지, 수수료 등에 따라 다를 수 있습니다.
              </p>
            </>
          ) : (
            <div style={{ marginTop: 24 }}>
              <p className="ed-fine">백테스트 결과를 불러오는 중입니다...</p>
              <button type="button" className="ed-btn ed-btn--line" style={{ marginTop: 14 }} onClick={goBacktestDetail}>
                상세 백테스트 보기 <ArrowRight size={16} />
              </button>
            </div>
          )}
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

        {/* 후기 */}
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
            <button type="button" className="ed-btn ed-btn--spark ed-btn--lg" onClick={goStart}>
              무료로 시작하기 <ArrowRight size={18} />
            </button>
          </div>
        </section>

        {/* 푸터 */}
        <footer className="ed-wrap ed-foot">
          <div className="ed-foot-grid">
            <Wordmark />
            <p className="ed-fine">
              HQA는 투자 자문이 아니며, 모든 투자 판단과 손실의 책임은 투자자
              본인에게 있습니다. 과거 수익률이 미래의 수익을 보장하지 않습니다.
            </p>
          </div>
          <p className="ed-fine" style={{ marginTop: 20 }}>
            © 2026 HQA — 곁에서 돌보는 자동매매
          </p>
        </footer>
      </div>
    </div>
  );
}
