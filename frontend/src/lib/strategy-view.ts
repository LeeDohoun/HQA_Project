import type { StrategyBatchStatus, StrategyHalt, StrategyPhase, StrategyStage } from "@/types/strategies";

export type PillTone = "neutral" | "good" | "warn" | "bad";

const STAGE_LABELS: Record<StrategyStage, string> = { observe: "관찰", shadow: "섀도", paper: "PAPER" };
const STAGE_RANK: Record<StrategyStage, number> = { observe: 0, shadow: 1, paper: 2 };

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "알 수 없음";
  return STAGE_LABELS[stage as StrategyStage] ?? stage;
}

/** Effective stage at the requested level reads as good; held below it reads as a warning. */
export function effectiveStageTone(requested: string, effective: string): PillTone {
  const want = STAGE_RANK[requested as StrategyStage];
  const got = STAGE_RANK[effective as StrategyStage];
  if (want === undefined || got === undefined) return "neutral";
  return got >= want ? "good" : "warn";
}

const VERDICT_LABELS: Record<string, string> = {
  pass: "통과", fail: "실패", inconclusive: "판단 보류", pending: "평가 중", none: "미평가",
  holdout_aborted: "보류 평가 중단", insufficient: "표본 부족", screening_fail: "예비 심사 탈락",
  delisting_sensitive: "상장폐지 민감", direction_only: "방향만 일치", diagnostic: "진단용", not_applicable: "해당 없음"
};

export function verdictLabel(verdict: string | null | undefined): string {
  if (!verdict) return "미평가";
  return VERDICT_LABELS[verdict.toLowerCase()] ?? verdict;
}

export function verdictTone(verdict: string | null | undefined): PillTone {
  const value = verdict?.toLowerCase();
  if (value === "pass") return "good";
  if (value === "fail") return "bad";
  return value && value !== "none" ? "warn" : "neutral";
}

// Readable text for stage-gate codes such as
// "holdout_not_passed:HF001_hegemony_filters/holdout_f_both:none". The raw code stays visible on screen.
const REASON_TEXT: Record<string, string> = {
  holdout_not_passed: "보류 구간 판정이 아직 통과가 아닙니다",
  holdout_missing: "보류 구간 판정 행이 지정되지 않았습니다",
  holdout_row_missing: "실험 대장에 보류 구간 판정 행이 없습니다",
  holdout_mismatch: "보류 구간 판정 행이 전략과 다릅니다",
  design_not_passed: "설계·검증 판정이 통과가 아닙니다",
  design_missing: "실험 대장에 설계·검증 판정이 없습니다",
  experiment_not_found: "실험 대장에서 실험을 찾지 못했습니다",
  registry_missing: "실험 대장을 읽지 못했습니다",
  registry_unreadable: "실험 대장을 읽지 못했습니다",
  shadow_sessions_insufficient: "섀도 기간이 부족합니다 (20거래일 이상 필요)",
  shadow_days_insufficient: "섀도 기간이 부족합니다 (달력 28일 이상 필요)",
  shadow_calendar_days_insufficient: "섀도 기간이 부족합니다 (달력 28일 이상 필요)",
  shadow_ledger_unreadable: "섀도 장부를 읽지 못했습니다",
  shadow_ledger_unverifiable: "섀도 장부를 검증하지 못했습니다",
  shadow_signal_mismatch: "섀도 신호가 결정 기록·KRX 일봉과 일치하지 않습니다",
  approvals_unreadable: "승인 기록을 읽지 못했습니다",
  shadow_too_short: "섀도 기간이 부족합니다 (20거래일·달력 28일 이상 필요)",
  shadow_not_started: "섀도 장부가 아직 시작되지 않았습니다",
  shadow_incidents: "섀도 기간에 운영 사고가 있었습니다",
  shadow_incident: "섀도 기간에 운영 사고가 있었습니다",
  shadow_mismatch: "섀도 체결이 결정 기록·KRX 일봉과 일치하지 않습니다",
  signal_mismatch: "섀도 신호가 결정 기록과 일치하지 않습니다",
  price_mismatch: "가상 체결 가격이 KRX 일봉과 일치하지 않습니다",
  approval_missing: "PAPER 승인 기록이 없습니다",
  paper_approval_missing: "PAPER 승인 기록이 없습니다",
  no_approval: "PAPER 승인 기록이 없습니다",
  decision_missing: "결정 기록이 없습니다",
  no_decision: "결정 기록이 없습니다",
  account_not_bound: "전략 계좌가 연결되지 않았습니다",
  early_paper_override: "사용자 결정으로 단계 조건 전에 PAPER(모의투자)를 시작했습니다",
  gate_bypassed: "충족하지 않은 채 넘어간 단계 조건 (아래 원문)",
  shadow_independent_check_failed: "섀도 체결이 FW001 체결 기록과 다릅니다",
  shadow_independent_check_unavailable: "FW001 체결 기록과 대조하지 못한 섀도 기록이 있습니다",
  decision_not_sealed: "결정 기록이 아직 봉인되지 않았습니다",
  decision_seal_late: "결정 기록이 마감(08:59) 이후 봉인돼 그달은 거래하지 않습니다",
  decision_seal_invalid: "결정 기록의 완료 증명이 손상됐거나 맞지 않습니다 (오류)",
  decision_hash_marker_mismatch: "결정 해시가 완료 증명과 다릅니다",
  unknown_stage: "알 수 없는 단계 요청입니다"
};

export function stageReasonText(code: string): string | null {
  const prefix = code.split(":", 1)[0].trim().toLowerCase();
  return REASON_TEXT[prefix] ?? null;
}

const BATCH_STATUS_LABELS: Record<StrategyBatchStatus, string> = {
  VALIDATED: "검증됨", QUEUED: "대기", SUBMITTING: "전송 중", SUBMITTED: "전송됨",
  COMPLETED: "완료", PARTIAL: "일부 체결", REJECTED: "거부", HALTED: "중단"
};

export function batchStatusLabel(status: string): string {
  return BATCH_STATUS_LABELS[status as StrategyBatchStatus] ?? status;
}

export function batchStatusTone(status: string): PillTone {
  if (status === "COMPLETED") return "good";
  if (status === "REJECTED" || status === "HALTED") return "bad";
  if (status === "PARTIAL") return "warn";
  return "neutral";
}

const PHASE_LABELS: Record<StrategyPhase, string> = { OPEN: "시가 (장전 동시호가)", CLOSE: "종가 (장 마감 동시호가)" };

export function phaseLabel(phase: string): string {
  return PHASE_LABELS[phase as StrategyPhase] ?? phase;
}

export function activeHalts(halts: StrategyHalt[] | null | undefined): StrategyHalt[] {
  return (halts ?? []).filter(halt => halt.halted);
}

export function haltScopeLabel(scope: string): string {
  return scope === "GLOBAL" ? "전체" : "전략 계좌";
}

/** Signed percentage, e.g. "+1.25%", "-0.40%", "0.00%". */
export function signedPct(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const rounded = Number(value.toFixed(digits));
  const sign = rounded > 0 ? "+" : rounded < 0 ? "-" : "";
  return `${sign}${Math.abs(rounded).toFixed(digits)}%`;
}

export function valueSign(value: number | null | undefined): "up" | "down" | "flat" {
  if (value == null || !Number.isFinite(value) || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

/**
 * SVG polyline points for a sparkline. Returns "" when fewer than two finite values exist.
 * A flat series draws a horizontal line through the middle.
 */
export function sparklinePoints(values: number[], width: number, height: number, pad = 2): string {
  const finite = values.filter(value => Number.isFinite(value));
  if (finite.length < 2) return "";
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const span = max - min;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  return finite.map((value, index) => {
    const x = pad + (index / (finite.length - 1)) * innerW;
    const y = span === 0 ? height / 2 : pad + (1 - (value - min) / span) * innerH;
    return `${Number(x.toFixed(2))},${Number(y.toFixed(2))}`;
  }).join(" ");
}
