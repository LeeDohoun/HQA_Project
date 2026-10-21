"use client";

import { redirect } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { StatusPill } from "@/components/common/status-pill";
import { useWorkspace } from "@/components/common/workspace-frame";
import { strategyApi } from "@/lib/api";
import {
  activeHalts, batchStatusLabel, batchStatusTone, effectiveStageTone, haltScopeLabel, phaseLabel, signedPct,
  sparklinePoints, stageLabel, stageReasonText, valueSign, verdictLabel, verdictTone
} from "@/lib/strategy-view";
import type {
  StrategiesResponse, StrategyLatestBatch, StrategyRisk, StrategyShadow, StrategyStatusPayload, StrategySummary
} from "@/types/strategies";
import styles from "./page.module.css";

const number = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : new Intl.NumberFormat("ko-KR").format(value);
const won = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : `${number(Math.round(value))}원`;
const pct = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(digits)}%`;
const dateTime = (value: string | null | undefined) => {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
};
const day = (value: string | null | undefined) => {
  if (!value) return "—";
  return /^\d{4}-\d{2}-\d{2}T/.test(value) ? dateTime(value) : value;
};

export default function StrategiesPage() {
  const { user, loadingUser } = useWorkspace();
  const [data, setData] = useState<StrategiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    setError("");
    try {
      const response = await strategyApi.list(controller.signal);
      if (!controller.signal.aborted) setData(response);
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : "전략 상태를 불러오지 못했습니다.");
    } finally {
      if (controllerRef.current === controller) {
        setLoading(false);
        controllerRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    if (loadingUser || !user) return;
    void load();
    return () => { controllerRef.current?.abort(); controllerRef.current = null; };
  }, [user?.id, loadingUser, load]);

  if (!loadingUser && !user) redirect("/login");
  if (loadingUser || !user) return <main className={styles.page}><p className={styles.muted}>불러오는 중...</p></main>;

  const halts = activeHalts(data?.halts);
  const strategies = data?.strategies ?? [];

  return (
    <main className={styles.page} aria-busy={loading}>
      <div className={styles.heading}>
        <div>
          <span className={styles.eyebrow}>STRATEGY RUNTIME</span>
          <h1>전략</h1>
          <p>연구를 거친 전략의 운용 단계, 최신 결정, 섀도 장부와 일괄 주문 현황입니다.</p>
        </div>
        <button className={styles.secondary} type="button" disabled={loading} onClick={() => void load()}>
          {loading ? "불러오는 중..." : "새로고침"}
        </button>
      </div>

      <p className={styles.note}>
        전략 계좌는 <b>PAPER(모의투자)</b> 전용이며 실제 자금으로 주문하지 않습니다. 연구·섀도 결과는 과거 자료와 가상 체결에 근거하며 미래 수익을 보장하지 않습니다.
      </p>

      {halts.length ? (
        <section className={styles.halt} role="alert" aria-labelledby="halt-title">
          <h2 id="halt-title">중단 스위치가 켜져 있습니다</h2>
          <p>새 일괄 주문 접수와 미전송 주문 전송이 모두 멈춘 상태입니다.</p>
          <ul>
            {halts.map(halt => (
              <li key={`${halt.scope}-${halt.updatedAt}`}>
                <b>{haltScopeLabel(halt.scope)}</b>
                {halt.scope !== "GLOBAL" ? <code className={styles.code}>{halt.scope}</code> : null}
                {" · "}{halt.reason || "사유 미기재"}
                <span className={styles.muted}> · {dateTime(halt.updatedAt)}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {error ? (
        <div className={styles.alert} role="alert">
          <span>{error}</span>
          <button className={styles.secondary} type="button" disabled={loading} onClick={() => void load()}>다시 시도</button>
        </div>
      ) : null}

      {loading && !data ? <p className={styles.muted} role="status">전략 상태를 불러오는 중...</p> : null}

      {data && !strategies.length ? <div className={styles.empty}>등록된 전략 상태가 없습니다.</div> : null}

      <div className={styles.list}>
        {strategies.map(strategy => <StrategyCard key={strategy.strategyId} strategy={strategy} />)}
      </div>
    </main>
  );
}

function StrategyCard({ strategy }: { strategy: StrategySummary }) {
  const payload = strategy.payload;
  const titleId = `strategy-${strategy.strategyId}`;
  if (!payload) {
    return (
      <article className={styles.card} aria-labelledby={titleId}>
        <h2 id={titleId}>{strategy.strategyId}</h2>
        <p className={styles.muted}>상태 기록이 비어 있습니다. 갱신 {dateTime(strategy.updatedAt)}</p>
      </article>
    );
  }
  return (
    <article className={styles.card} aria-labelledby={titleId}>
      <header className={styles.cardHead}>
        <div>
          <h2 id={titleId}>{payload.title || strategy.strategyId}</h2>
          <code className={styles.code}>{strategy.strategyId}</code>
          {payload.description ? <p className={styles.description}>{payload.description}</p> : null}
        </div>
        <p className={styles.fine}>상태 생성 {dateTime(payload.generatedAt)}<br />수신 {dateTime(strategy.updatedAt)}</p>
      </header>

      <div className={styles.grid}>
        <StageSection payload={payload} />
        <ResearchSection payload={payload} />
        <DecisionSection payload={payload} />
        {payload.portfolio ? <PortfolioSection payload={payload} /> : null}
        <ShadowSection shadow={payload.shadow} />
        <PlanSection payload={payload} />
        <BatchSection batch={strategy.latestBatch} />
        <AccountSection payload={payload} risk={strategy.risk} />
      </div>
    </article>
  );
}

function StageSection({ payload }: { payload: StrategyStatusPayload }) {
  const stage = payload.stage;
  const reasons = stage?.reasons ?? [];
  return (
    <section className={`${styles.block} ${styles.wide}`}>
      <h3>운용 단계</h3>
      <div className={styles.stages}>
        <span className={styles.stageItem}><span className={styles.label}>요청</span><StatusPill label={stageLabel(stage?.requested)} /></span>
        <span aria-hidden="true" className={styles.arrow}>→</span>
        <span className={styles.stageItem}><span className={styles.label}>실제</span>
          <StatusPill label={stageLabel(stage?.effective)} tone={effectiveStageTone(stage?.requested ?? "", stage?.effective ?? "")} /></span>
      </div>
      {stage?.earlyPaper ? (
        <p className={styles.fine} data-alert="true">
          조기 PAPER: 단계 조건을 채우기 전에 모의투자를 시작했습니다 ({stage.earlyPaper.decided_at.slice(0, 10)}, {stage.earlyPaper.reason}).
          이 기간 성과는 실계좌 판단 근거로 바로 쓰지 않습니다.
        </p>
      ) : null}
      {reasons.length ? (
        <>
          <p className={styles.label}>{stage?.earlyPaper ? "충족하지 않은 단계 조건" : "막힌 이유"}</p>
          <ul className={styles.reasons}>
            {reasons.map(code => {
              const text = stageReasonText(code);
              return <li key={code}>{text ? <span>{text}</span> : null}<code className={styles.code}>{code}</code></li>;
            })}
          </ul>
        </>
      ) : <p className={styles.muted}>막힌 이유 없음</p>}
    </section>
  );
}

function ResearchSection({ payload }: { payload: StrategyStatusPayload }) {
  const research = payload.research;
  const design = research?.design;
  const holdout = research?.holdout;
  const holdoutRow = holdout?.experimentId ? `${holdout.experimentId}${holdout.variant ? ` / ${holdout.variant}` : ""}` : null;
  return (
    <section className={styles.block}>
      <h3>연구 판정</h3>
      {research?.experimentId ? <p className={styles.fine}><code className={styles.code}>{research.experimentId}{research.variant ? ` / ${research.variant}` : ""}</code></p> : null}
      <dl className={styles.info}>
        <div>
          <dt>설계·검증</dt>
          <dd><StatusPill label={verdictLabel(design?.verdict)} tone={verdictTone(design?.verdict)} />
            {design?.recordedAt ? <span className={styles.muted}> {dateTime(design.recordedAt)}</span> : null}</dd>
        </div>
        <div>
          <dt>보류 구간</dt>
          <dd><StatusPill label={verdictLabel(holdout?.verdict)} tone={verdictTone(holdout?.verdict)} />
            {holdout?.recordedAt ? <span className={styles.muted}> {dateTime(holdout.recordedAt)}</span> : null}
            {holdoutRow ? <code className={styles.code}>{holdoutRow}</code> : null}</dd>
        </div>
      </dl>
    </section>
  );
}

function DecisionSection({ payload }: { payload: StrategyStatusPayload }) {
  const decision = payload.decision;
  return (
    <section className={styles.block}>
      <h3>최신 결정</h3>
      {decision ? (
        <dl className={styles.info}>
          <div><dt>결정일</dt><dd>{day(decision.decisionDate)}</dd></div>
          <div><dt>진입일</dt><dd>{day(decision.tradeDate)}</dd></div>
          {decision.exitDate ? <div><dt>청산일</dt><dd>{day(decision.exitDate)}</dd></div> : null}
          <div><dt>목표 종목</dt><dd>{number(decision.targetCount)}종목</dd></div>
          <div><dt>기록 구분</dt><dd><code className={styles.code}>{decision.label}</code>{decision.month ? <span className={styles.muted}> {decision.month}</span> : null}</dd></div>
        </dl>
      ) : <p className={styles.muted}>결정 기록 없음</p>}
    </section>
  );
}

const TERMINATION_TEXT: Record<string, string> = {
  liquidating: "종료됨 · 보유 종목 청산 중",
  terminated_drawdown: "종료됨 · 청산 완료 (현금 보유)"
};

function indexRows(indices: unknown): { name: string; date: string; close: number }[] {
  if (Array.isArray(indices)) {
    return indices.filter((row): row is { name: string; date: string; close: number } =>
      !!row && typeof row === "object" && typeof (row as { close?: unknown }).close === "number");
  }
  if (indices && typeof indices === "object") {
    return Object.entries(indices as Record<string, unknown>).flatMap(([name, value]) => {
      if (value && typeof value === "object") {
        const row = value as { close?: unknown; date?: string };
        const close = typeof row.close === "number" ? row.close : Number(String(row.close ?? "").replace(/,/g, ""));
        return Number.isFinite(close) && row.close != null ? [{ name, date: row.date ?? "", close }] : [];
      }
      return typeof value === "number" ? [{ name, date: "", close: value }] : [];
    });
  }
  return [];
}

function PortfolioSection({ payload }: { payload: StrategyStatusPayload }) {
  const portfolio = payload.portfolio;
  const termination = payload.termination;
  if (!portfolio) return null;
  const indices = indexRows(portfolio.indices);
  const drawdown = portfolio.drawdownPct;
  return (
    <section className={styles.block}>
      <h3>계좌 전체</h3>
      {termination?.terminated ? (
        <p className={styles.fine} data-alert="true">
          {TERMINATION_TEXT[termination.state ?? ""] ?? "종료됨"} ({termination.reason ?? "사유 미기록"}, {day(termination.terminatedAt)})
        </p>
      ) : null}
      <p className={styles.figure}>{won(portfolio.navKrw)}</p>
      <dl className={styles.info}>
        <div><dt>고점</dt><dd>{won(portfolio.runningMaxNavKrw)}</dd></div>
        <div><dt>고점 대비 낙폭</dt><dd data-alert={drawdown != null && drawdown >= 20 ? "true" : undefined}>
          {drawdown == null ? "—" : `${drawdown.toFixed(2)}%`} <span className={styles.muted}>(25%에서 종료)</span></dd></div>
        <div><dt>기준</dt><dd>{portfolio.source === "krx_official_close" || portfolio.source === "krx_official" ? "공식 종가 순자산" : portfolio.source === "shadow_ledger" ? "가상 장부 (활성화 전)" : (portfolio.source ?? "—")}</dd></div>
        {indices.map(row => (
          <div key={row.name}><dt>{row.name}</dt><dd>{number(row.close)}{row.date ? <span className={styles.muted}> {row.date}</span> : null}</dd></div>
        ))}
      </dl>
      {payload.sleeveDetailSealed ? <p className={styles.fine}>부문별 손익과 비교 원장은 최종 평가 전까지 공개하지 않습니다 (OP001·BB003 열람 분리).</p> : null}
    </section>
  );
}

function ShadowSection({ shadow }: { shadow: StrategyShadow | null | undefined }) {
  if (!shadow) {
    return <section className={styles.block}><h3>섀도 장부</h3><p className={styles.muted}>섀도 장부 없음</p></section>;
  }
  const history = Array.isArray(shadow.history) ? shadow.history : [];
  return (
    <section className={styles.block}>
      <h3>섀도 장부</h3>
      <p className={styles.figure}>{won(shadow.navKrw)}
        <span className={styles.change} data-sign={valueSign(shadow.returnPct)}>{signedPct(shadow.returnPct)}</span></p>
      <Sparkline history={history} />
      <dl className={styles.info}>
        <div><dt>기록 단계</dt><dd>{stageLabel(shadow.mode)}</dd></div>
        <div><dt>섀도 세션</dt><dd>{number(shadow.sessions)}회{shadow.firstDate ? <span className={styles.muted}> {shadow.firstDate} ~ {shadow.lastDate ?? ""}</span> : null}</dd></div>
        <div><dt>전체 기록</dt><dd>{number(history.length)}회{history.length ? <span className={styles.muted}> {history[0].date} ~ {history[history.length - 1].date}</span> : null}</dd></div>
        <div><dt>시작 순자산</dt><dd>{won(shadow.initialNavKrw)}</dd></div>
        <div><dt>운영 사고</dt><dd data-alert={shadow.incidents > 0 ? "true" : undefined}>{number(shadow.incidents)}건</dd></div>
      </dl>
      {shadow.mode === "observe" ? <p className={styles.fine}>관찰 단계 기록은 섀도 증거로 세지 않습니다.</p> : null}
    </section>
  );
}

const SPARK_W = 240;
const SPARK_H = 48;

function Sparkline({ history }: { history: StrategyShadow["history"] }) {
  const values = history.map(point => point.navKrw);
  const points = sparklinePoints(values, SPARK_W, SPARK_H);
  if (!points) return <p className={styles.fine}>추이를 그릴 기록이 부족합니다.</p>;
  const first = history[0];
  const last = history[history.length - 1];
  const description = `섀도 순자산 추이 ${history.length}개 세션: ${first.date} ${won(first.navKrw)}에서 ${last.date} ${won(last.navKrw)}`;
  const sign = last.navKrw > first.navKrw ? "up" : last.navKrw < first.navKrw ? "down" : "flat";
  return (
    <svg className={styles.spark} data-sign={sign} viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none" role="img" aria-label={description}>
      <title>{description}</title>
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function PlanSection({ payload }: { payload: StrategyStatusPayload }) {
  const plan = payload.plan;
  return (
    <section className={styles.block}>
      <h3>다음 주문 계획</h3>
      {plan ? (
        <dl className={styles.info}>
          <div><dt>세션</dt><dd>{day(plan.sessionDate)} · {phaseLabel(plan.phase)}</dd></div>
          <div><dt>매도</dt><dd>{number(plan.sellCount)}건 · {won(plan.sellNotionalKrw)}</dd></div>
          <div><dt>매수</dt><dd>{number(plan.buyCount)}건 · {won(plan.buyNotionalKrw)}</dd></div>
          <div><dt>거래대금 경고</dt><dd data-alert={plan.advWarnings > 0 ? "true" : undefined}>{number(plan.advWarnings)}건</dd></div>
        </dl>
      ) : <p className={styles.muted}>예정된 주문 없음</p>}
      {plan && plan.advWarnings > 0 ? <p className={styles.fine}>20일 평균 거래대금의 1%를 넘는 주문입니다. 사전 등록 규칙이므로 주문을 줄이지 않습니다.</p> : null}
    </section>
  );
}

function BatchSection({ batch }: { batch: StrategyLatestBatch | null }) {
  return (
    <section className={styles.block}>
      <h3>최근 일괄 주문</h3>
      {batch ? (
        <dl className={styles.info}>
          <div><dt>상태</dt><dd><StatusPill label={batchStatusLabel(batch.status)} tone={batchStatusTone(batch.status)} /> <code className={styles.code}>{batch.status}</code></dd></div>
          <div><dt>세션</dt><dd>{day(batch.sessionDate)} · {phaseLabel(batch.phase)}</dd></div>
          <div><dt>주문·체결·거부</dt><dd>{number(batch.orderCount)} · {number(batch.filledCount)} · <span data-alert={batch.rejectedCount > 0 ? "true" : undefined}>{number(batch.rejectedCount)}</span></dd></div>
          <div><dt>평균 슬리피지</dt><dd>{batch.avgSlippagePct == null ? "—" : signedPct(batch.avgSlippagePct, 3)}</dd></div>
        </dl>
      ) : <p className={styles.muted}>일괄 주문 기록 없음</p>}
      {batch?.avgSlippagePct != null ? <p className={styles.fine}>기준가 대비 체결가 차이의 평균입니다. 불리한 쪽이 양수입니다.</p> : null}
    </section>
  );
}

function AccountSection({ payload, risk }: { payload: StrategyStatusPayload; risk: StrategyRisk | null }) {
  const bound = !!payload.account?.bound;
  return (
    <section className={styles.block}>
      <h3>전략 계좌 · 위험 한도</h3>
      <p><StatusPill label={bound ? "계좌 연결됨" : "계좌 미연결"} tone={bound ? "good" : "neutral"} /></p>
      {risk ? (
        <>
          <dl className={styles.info}>
            <div><dt>순자산</dt><dd>{won(risk.equityKrw)}</dd></div>
            <div><dt>일일 손익</dt><dd>{signedPct(risk.dailyPnlPct)} <span className={styles.muted}>/ 한도 -{pct(risk.limits?.dailyLossLimitPct, 1)}</span></dd></div>
            <div><dt>고점 대비 낙폭</dt><dd>{pct(risk.drawdownPct)} <span className={styles.muted}>/ 중단 {pct(risk.limits?.drawdownStopPct, 1)}</span></dd></div>
            <div><dt>오늘 주문</dt><dd>{number(risk.ordersToday)}건 <span className={styles.muted}>/ 상한 {number(risk.limits?.maxOrdersPerDay)}건 · 취소 {number(risk.cancelsToday)}건</span></dd></div>
            <div><dt>매수</dt><dd><StatusPill label={risk.buyAllowed ? "허용" : "차단"} tone={risk.buyAllowed ? "good" : "bad"} />
              {risk.halted ? <> <StatusPill label="중단됨" tone="bad" /></> : null}
              {risk.fullReviewRequired ? <> <StatusPill label="전체 검토 필요" tone="warn" /></> : null}</dd></div>
          </dl>
          {risk.reasons?.length ? <ul className={styles.reasons}>{risk.reasons.map(reason => <li key={reason}><code className={styles.code}>{reason}</code></li>)}</ul> : null}
        </>
      ) : <p className={styles.muted}>{bound ? "위험 상태 기록 없음" : "계좌가 연결되면 위험 한도 사용률을 표시합니다."}</p>}
    </section>
  );
}
