import type { AnalysisResult, ScoreDetail } from "@/types/api";

const INK = "var(--ink, var(--text-bright))";
const INK2 = "var(--ink-2, var(--text))";
const INK3 = "var(--ink-3, var(--muted))";
const RULE = "1px solid var(--rule, var(--line))";

const AGENTS: Record<string, { label: string; focus: string }> = {
  analyst: { label: "애널리스트", focus: "공시·뉴스" },
  quant: { label: "퀀트", focus: "재무·가치평가" },
  chartist: { label: "차티스트", focus: "주가 흐름" },
  risk_manager: { label: "리스크 매니저", focus: "계좌 판단" },
  quick_decision: { label: "빠른 판단", focus: "" }
};

// Fields shown in their own sections; the thesis is already the opinion paragraph.
const SHOWN_DETAIL_KEYS = new Set(["stock_code", "role", "score", "confidence", "thesis", "risks", "data_gaps", "citations"]);

function agentLabel(agent: string) {
  return AGENTS[agent]?.label ?? agent.charAt(0).toUpperCase() + agent.slice(1);
}

function agentFocus(agent: string) {
  return AGENTS[agent]?.focus ?? "";
}

function valueText(value: unknown): string {
  if (value == null || value === "") return "-";
  if (Array.isArray(value)) return value.length ? value.map(valueText).join(", ") : "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function textList(value: unknown): string[] {
  if (value == null || value === "") return [];
  const items = Array.isArray(value) ? value : [value];
  return items.map((item) => (typeof item === "string" ? item : valueText(item)).trim()).filter(Boolean);
}

function claimList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object" && "claim" in item) return String((item as { claim?: unknown }).claim ?? "");
      return "";
    })
    .map((claim) => claim.trim())
    .filter(Boolean);
}

function confidenceOf(score: ScoreDetail): number | null {
  const value = score.details?.confidence;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function decisionValue(decision: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = decision[key];
    if (value != null && String(value).trim() !== "") return value;
  }
  return null;
}

function ScoreBar({ score }: { score: ScoreDetail }) {
  const max = score.maxScore > 0 ? score.maxScore : 100;
  const percent = Math.max(0, Math.min(100, (score.totalScore / max) * 100));
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(96px, 150px) 1fr auto", alignItems: "center", gap: 10 }}>
      <span style={{ fontWeight: 700, color: INK, fontSize: ".92rem" }}>
        {agentLabel(score.agent)}
        {agentFocus(score.agent) ? (
          <small style={{ display: "block", color: INK3, fontWeight: 600, fontSize: ".74rem" }}>{agentFocus(score.agent)}</small>
        ) : null}
      </span>
      <span style={{ height: 8, background: "var(--rule, var(--line))", borderRadius: 4, overflow: "hidden" }}>
        <span style={{ display: "block", width: `${percent}%`, height: "100%", background: "var(--moss, var(--accent))", borderRadius: 4 }} />
      </span>
      <span style={{ fontWeight: 800, color: INK, fontVariantNumeric: "tabular-nums", minWidth: 28, textAlign: "right" }}>
        {score.totalScore}
      </span>
    </div>
  );
}

function withSubject(word: string): string {
  const code = word.charCodeAt(word.length - 1);
  const hasFinalConsonant = code >= 0xac00 && code <= 0xd7a3 && (code - 0xac00) % 28 !== 0;
  return `${word}${hasFinalConsonant ? "이" : "가"}`;
}

function agreementText(scores: ScoreDetail[]): string | null {
  if (scores.length < 2) return null;
  const sorted = [...scores].sort((a, b) => b.totalScore - a.totalScore);
  const high = sorted[0];
  const low = sorted[sorted.length - 1];
  if (high.totalScore - low.totalScore >= 20) {
    return `의견이 갈립니다. ${withSubject(agentLabel(high.agent))} ${high.totalScore}점으로 가장 높게, ${withSubject(agentLabel(low.agent))} ${low.totalScore}점으로 가장 낮게 평가했습니다.`;
  }
  return `세 전문가의 평가가 ${low.totalScore}~${high.totalScore}점으로 비슷합니다.`;
}

export function AnalysisSummaryCard({ result }: { result: AnalysisResult }) {
  if (result.finalDecision == null) {
    const scores = (result.scores ?? []).filter((score) => Number.isFinite(score.totalScore));
    if (!scores.length) {
      // While the experts are still working there is no score yet; say so instead of showing "- / 100".
      const finished = result.status === "completed" || result.status === "failed";
      return (
        <p style={{ margin: "16px 0 0", color: INK3, lineHeight: 1.6, fontSize: ".9rem" }}>
          {finished ? "전문가 점수가 없습니다." : "분석이 끝나면 전문가 점수가 여기에 표시됩니다."}
        </p>
      );
    }
    const average = scores.reduce((sum, score) => sum + score.totalScore, 0) / scores.length;
    const agreement = agreementText(scores);
    return (
      <div style={{ marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: "2.4rem", fontWeight: 800, lineHeight: 1, color: INK, fontVariantNumeric: "tabular-nums" }}>
            {average.toFixed(1)}
          </span>
          <span style={{ color: INK3, fontWeight: 700 }}>/ 100 · 전문가 평균 점수</span>
        </div>
        <div style={{ display: "grid", gap: 10, marginTop: 16 }}>
          {scores.map((score) => <ScoreBar key={score.agent} score={score} />)}
        </div>
        {agreement ? (
          <p style={{ margin: "14px 0 0", color: INK2, lineHeight: 1.7, fontSize: ".95rem" }}>{agreement}</p>
        ) : null}
        <p style={{ margin: "8px 0 0", color: INK3, lineHeight: 1.6, fontSize: ".85rem" }}>
          전문가별 근거와 위험 요인은 아래에서 펼쳐 볼 수 있습니다. 이 분석은 주문을 내지 않으며, 계좌별 매매 판단은 자동매매가 따로 합니다.
        </p>
      </div>
    );
  }
  const decision = result.finalDecision ?? {};
  const score = decisionValue(decision, ["total_score", "totalScore"]);
  const action = decisionValue(decision, ["action", "action_code", "actionCode"]);
  const confidence = decisionValue(decision, ["confidence"]);
  const risk = decisionValue(decision, ["risk_level", "riskLevel", "risk_level_code", "riskLevelCode"]);
  const summary = decisionValue(decision, ["summary", "detailed_reasoning", "detailedReasoning"]);

  return (
    <div style={{ border: RULE, background: "var(--card, var(--surface))", padding: 16, marginTop: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div>
          <p style={{ margin: "0 0 6px", color: INK3, fontSize: ".78rem", fontWeight: 800 }}>최종 판단</p>
          <p style={{ margin: 0, fontSize: "1.25rem", fontWeight: 800, color: INK }}>
            {valueText(action)}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <MetricPill label={`점수 ${valueText(score)}`} />
          <MetricPill label={`확신 ${valueText(confidence)}`} />
          <MetricPill label={`리스크 ${valueText(risk)}`} />
        </div>
      </div>
      {summary ? (
        <p style={{ margin: "12px 0 0", color: INK2, lineHeight: 1.6 }}>
          {valueText(summary)}
        </p>
      ) : null}
    </div>
  );
}

function MetricPill({ label }: { label: string }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        minHeight: 26,
        padding: "0 9px",
        border: RULE,
        color: INK2,
        fontSize: ".76rem",
        fontWeight: 800
      }}
    >
      {label}
    </span>
  );
}

function DetailList({ title, items, muted = false }: { title: string; items: string[]; muted?: boolean }) {
  if (!items.length) return null;
  return (
    <div style={{ marginTop: 14 }}>
      <p style={{ margin: "0 0 4px", fontSize: ".8rem", fontWeight: 800, color: INK3 }}>{title}</p>
      <ul style={{ margin: 0, paddingLeft: 18, color: muted ? INK3 : INK2, fontSize: ".9rem", lineHeight: 1.65 }}>
        {items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}
      </ul>
    </div>
  );
}

export function AgentDetailSections({ scores }: { scores: ScoreDetail[] }) {
  if (!scores.length) {
    return <p style={{ marginTop: 14, color: INK3 }}>에이전트별 분석 결과가 없습니다.</p>;
  }

  return (
    <div style={{ display: "grid", gap: 10, marginTop: 16 }}>
      {scores.map((score) => {
        const details = score.details ?? {};
        const confidence = confidenceOf(score);
        const claims = claimList(details.citations);
        const other = Object.entries(details).filter(([key]) => !SHOWN_DETAIL_KEYS.has(key));
        return (
          <details
            key={score.agent}
            style={{ border: RULE, background: "var(--paper, var(--surface-2))", padding: "12px 14px" }}
          >
            <summary style={{ cursor: "pointer", fontWeight: 800, color: INK, fontSize: ".98rem" }}>
              {agentLabel(score.agent)}
              {/* nowrap keeps "58 / 100" and "확신 55" whole on narrow phones; each group wraps as a unit. */}
              {agentFocus(score.agent) ? (
                <span style={{ color: INK3, fontWeight: 600, whiteSpace: "nowrap" }}> · {agentFocus(score.agent)}</span>
              ) : null}
              <span style={{ marginLeft: 8, whiteSpace: "nowrap" }}>{score.totalScore} / {score.maxScore}</span>
              {confidence != null ? (
                <span style={{ marginLeft: 8, color: INK3, fontWeight: 600, fontSize: ".85rem", whiteSpace: "nowrap" }}>확신 {confidence}</span>
              ) : null}
              {score.grade ? ` · ${score.grade}` : ""}
            </summary>
            {score.opinion ? (
              <p style={{ margin: "12px 0 0", color: INK2, lineHeight: 1.75, fontSize: ".95rem" }}>
                {score.opinion}
              </p>
            ) : null}
            <DetailList title="위험 요인" items={textList(details.risks)} />
            <DetailList title="부족한 데이터" items={textList(details.data_gaps)} muted />
            {claims.length ? (
              <details style={{ marginTop: 14 }}>
                <summary style={{ cursor: "pointer", color: INK3, fontWeight: 700, fontSize: ".85rem" }}>
                  근거 {claims.length}건 보기
                </summary>
                <ol style={{ margin: "8px 0 0", paddingLeft: 20, color: INK2, fontSize: ".86rem", lineHeight: 1.65 }}>
                  {claims.map((claim, index) => <li key={`${score.agent}-claim-${index}`}>{claim}</li>)}
                </ol>
              </details>
            ) : null}
            {other.length ? (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                  border: RULE,
                  marginTop: 12
                }}
              >
                {other.map(([key, value]) => (
                  <div
                    key={`${score.agent}-${key}`}
                    style={{ padding: 10, borderRight: RULE, borderBottom: RULE }}
                  >
                    <small style={{ display: "block", color: INK3, fontSize: ".72rem" }}>{key}</small>
                    <span style={{ display: "block", marginTop: 4, color: INK, fontSize: ".82rem", overflowWrap: "anywhere" }}>
                      {valueText(value)}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </details>
        );
      })}
    </div>
  );
}
