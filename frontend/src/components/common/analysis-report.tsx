import type { AnalysisResult, ScoreDetail } from "@/types/api";

function agentLabel(agent: string) {
  switch (agent) {
    case "analyst": return "Analyst";
    case "quant": return "Quant";
    case "chartist": return "Chartist";
    case "risk_manager": return "RiskManager";
    case "quick_decision": return "QuickDecision";
    default: return agent.charAt(0).toUpperCase() + agent.slice(1);
  }
}

function valueText(value: unknown): string {
  if (value == null || value === "") return "-";
  if (Array.isArray(value)) return value.length ? value.map(valueText).join(", ") : "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function decisionValue(decision: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = decision[key];
    if (value != null && String(value).trim() !== "") return value;
  }
  return null;
}

export function AnalysisSummaryCard({ result }: { result: AnalysisResult }) {
  const decision = result.finalDecision ?? {};
  const score = decisionValue(decision, ["total_score", "totalScore"]);
  const action = decisionValue(decision, ["action", "action_code", "actionCode"]);
  const confidence = decisionValue(decision, ["confidence"]);
  const risk = decisionValue(decision, ["risk_level", "riskLevel", "risk_level_code", "riskLevelCode"]);
  const summary = decisionValue(decision, ["summary", "detailed_reasoning", "detailedReasoning"]);

  return (
    <div style={{ border: "1px solid var(--rule, var(--line))", background: "var(--card, var(--surface))", padding: 16, marginTop: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div>
          <p style={{ margin: "0 0 6px", color: "var(--ink-3, var(--muted))", fontSize: ".78rem", fontWeight: 800 }}>최종 판단</p>
          <p style={{ margin: 0, fontSize: "1.25rem", fontWeight: 800, color: "var(--ink, var(--text-bright))" }}>
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
        <p style={{ margin: "12px 0 0", color: "var(--ink-2, var(--text))", lineHeight: 1.6 }}>
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
        border: "1px solid var(--rule, var(--line))",
        color: "var(--ink-2, var(--text))",
        fontSize: ".76rem",
        fontWeight: 800
      }}
    >
      {label}
    </span>
  );
}

export function AgentDetailSections({ scores }: { scores: ScoreDetail[] }) {
  if (!scores.length) {
    return <p style={{ marginTop: 14, color: "var(--ink-3, var(--muted))" }}>에이전트별 분석 결과가 없습니다.</p>;
  }

  return (
    <div style={{ display: "grid", gap: 10, marginTop: 16 }}>
      {scores.map((score) => (
        <details
          key={score.agent}
          style={{ border: "1px solid var(--rule, var(--line))", background: "var(--paper, var(--surface-2))", padding: "12px 14px" }}
        >
          <summary style={{ cursor: "pointer", fontWeight: 800, color: "var(--ink, var(--text-bright))" }}>
            {agentLabel(score.agent)} · {score.totalScore} / {score.maxScore}
            {score.grade ? ` · ${score.grade}` : ""}
          </summary>
          {score.opinion ? (
            <p style={{ margin: "10px 0 0", color: "var(--ink-2, var(--text))", lineHeight: 1.6 }}>
              {score.opinion}
            </p>
          ) : null}
          {Object.keys(score.details ?? {}).length > 0 ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                border: "1px solid var(--rule, var(--line))",
                marginTop: 12
              }}
            >
              {Object.entries(score.details).map(([key, value]) => (
                <div
                  key={`${score.agent}-${key}`}
                  style={{ padding: 10, borderRight: "1px solid var(--rule, var(--line))", borderBottom: "1px solid var(--rule, var(--line))" }}
                >
                  <small style={{ display: "block", color: "var(--ink-3, var(--muted))", fontSize: ".72rem" }}>{key}</small>
                  <span style={{ display: "block", marginTop: 4, color: "var(--ink, var(--text-bright))", fontSize: ".82rem", overflowWrap: "anywhere" }}>
                    {valueText(value)}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </details>
      ))}
    </div>
  );
}
