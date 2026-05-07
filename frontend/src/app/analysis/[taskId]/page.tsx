"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/common/app-shell";
import { StatusPill } from "@/components/common/status-pill";
import { analysisApi, eventStreamUrl } from "@/lib/api";
import { formatDate, titleCaseAgent } from "@/lib/format";
import type { AnalysisProgressEvent, AnalysisResult } from "@/types/api";

export default function AnalysisPage() {
  const params = useParams<{ taskId: string }>();
  const taskId = params.taskId;
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [progress, setProgress] = useState<AnalysisProgressEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let eventSource: EventSource | null = null;

    async function loadResult() {
      try {
        const response = await analysisApi.result(taskId);
        if (!active) return;
        setResult(response);
        if (response.status === "completed" || response.status === "failed") {
          setLoading(false);
          return;
        }
      } catch (loadError) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "분석 결과를 불러오지 못했습니다.");
          setLoading(false);
        }
        return;
      }

      try {
        eventSource = new EventSource(eventStreamUrl(`/api/v1/analysis/${taskId}/stream`), {
          withCredentials: true
        });

        eventSource.addEventListener("progress", (event) => {
          if (!active) return;
          setProgress(JSON.parse((event as MessageEvent<string>).data) as AnalysisProgressEvent);
        });

        eventSource.addEventListener("completed", async () => {
          const latest = await analysisApi.result(taskId);
          if (!active) return;
          setResult(latest);
          setLoading(false);
          eventSource?.close();
        });

        eventSource.onerror = async () => {
          try {
            const latest = await analysisApi.result(taskId);
            if (active) {
              setResult(latest);
              if (latest.status === "completed" || latest.status === "failed") {
                setLoading(false);
                eventSource?.close();
              }
            }
          } catch {
            if (active) setLoading(false);
          }
        };
      } catch {
        setLoading(false);
      }
    }

    loadResult().finally(() => {
      if (active && !eventSource) setLoading(false);
    });

    return () => {
      active = false;
      eventSource?.close();
    };
  }, [taskId]);

  const statusTone = useMemo(() => {
    if (result?.status === "completed") return "good";
    if (result?.status === "failed") return "bad";
    return "warn";
  }, [result?.status]);

  return (
    <AppShell
      title="분석 결과"
      actions={<Link className="button-ghost" href="/dashboard">대시보드로 돌아가기</Link>}
    >
      {error ? <p className="error-text">{error}</p> : null}

      <div className="panel-grid">
        {/* Header panel */}
        <section className="panel" style={{ gridColumn: "span 12" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
            <div>
              <h2 style={{ margin: "0 0 4px", fontSize: "1.1rem", fontWeight: 700, color: "var(--text-bright)" }}>
                {result?.stock?.name ?? "로딩 중..."}
              </h2>
              <p className="meta mono" style={{ margin: 0 }}>{result?.stock?.code ?? taskId}</p>
            </div>
            <StatusPill label={translateStatus(result?.status ?? "running")} tone={statusTone as "good" | "warn" | "bad"} />
          </div>

          {progress ? (
            <div style={{ marginTop: 20, display: "grid", gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontSize: "0.82rem", color: "var(--muted)" }}>{progress.message}</span>
                <span style={{ fontSize: "0.82rem", color: "var(--accent)", fontWeight: 600 }}>{Math.round(progress.progress * 100)}%</span>
              </div>
              <div className="progress-bar">
                <span style={{ width: `${Math.round(progress.progress * 100)}%` }} />
              </div>
            </div>
          ) : null}

          <div className="stat-strip" style={{ marginTop: 20, gap: 8 }}>
            <div className="stat">
              <span className="meta">모드</span>
              <strong>{translateMode(result?.mode) ?? "-"}</strong>
            </div>
            <div className="stat">
              <span className="meta">생성</span>
              <strong style={{ fontSize: "0.88rem" }}>{formatDate(result?.createdAt)}</strong>
            </div>
            <div className="stat">
              <span className="meta">완료</span>
              <strong style={{ fontSize: "0.88rem" }}>{formatDate(result?.completedAt)}</strong>
            </div>
          </div>
        </section>

        {/* Scores */}
        <section className="panel" style={{ gridColumn: "span 7" }}>
          <h3 className="section-title">점수</h3>
          {result?.scores?.length ? (
            <div className="score-list">
              {result.scores.map((score) => (
                <div className="score-card" key={score.agent}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                    <strong style={{ fontSize: "0.9rem", color: "var(--text-bright)" }}>{titleCaseAgent(score.agent)}</strong>
                    <StatusPill label={score.grade ?? "-"} tone="neutral" />
                  </div>
                  <p style={{ margin: "6px 0 0", fontSize: "0.8rem", color: "var(--muted)" }}>
                    {score.totalScore} / {score.maxScore}
                  </p>
                  {score.opinion ? (
                    <p style={{ margin: "8px 0 0", fontSize: "0.85rem", color: "var(--text)", lineHeight: 1.5 }}>{score.opinion}</p>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state"><strong>점수 없음</strong></div>
          )}
        </section>

        {/* Final Decision */}
        <section className="panel" style={{ gridColumn: "span 5" }}>
          <h3 className="section-title">최종 판단</h3>
          {result?.finalDecision && Object.keys(result.finalDecision).length > 0 ? (
            <div className="detail-list">
              {Object.entries(result.finalDecision).map(([key, value]) => (
                <div className="detail-item" key={key}>
                  <span style={{ fontSize: "0.75rem", color: "var(--muted)", fontWeight: 500 }}>{key}</span>
                  <div style={{ marginTop: 4, fontSize: "0.88rem", color: "var(--text-bright)" }}>{String(value)}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state"><strong>판단 없음</strong></div>
          )}
        </section>

        {/* Warnings */}
        <section className="panel" style={{ gridColumn: "span 6" }}>
          <h3 className="section-title">경고</h3>
          {result?.qualityWarnings?.length ? (
            <div className="detail-list">
              {result.qualityWarnings.map((warning) => (
                <div className="detail-item" key={warning} style={{ borderColor: "rgba(245,158,11,0.2)", background: "var(--warn-dim)" }}>
                  <span style={{ fontSize: "0.85rem", color: "var(--warn)" }}>{warning}</span>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--muted)" }}>경고 없음</p>
          )}
        </section>

        {/* Errors */}
        <section className="panel" style={{ gridColumn: "span 6" }}>
          <h3 className="section-title">오류</h3>
          {result?.errors && Object.keys(result.errors).length > 0 ? (
            <div className="detail-list">
              {Object.entries(result.errors).map(([key, value]) => (
                <div className="detail-item" key={key} style={{ borderColor: "rgba(239,83,80,0.2)", background: "var(--bad-dim)" }}>
                  <span style={{ fontSize: "0.75rem", color: "var(--bad)", fontWeight: 500 }}>{key}</span>
                  <div style={{ marginTop: 4, fontSize: "0.85rem", color: "var(--text)" }}>{value}</div>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--muted)" }}>
              {loading ? "로딩 중..." : "오류 없음"}
            </p>
          )}
        </section>
      </div>
    </AppShell>
  );
}

function translateStatus(status: string) {
  switch (status) {
    case "pending": return "대기 중";
    case "running": return "진행 중";
    case "completed": return "완료";
    case "failed": return "실패";
    default: return status;
  }
}

function translateMode(mode?: string | null) {
  if (mode === "full") return "전체 분석";
  if (mode === "quick") return "빠른 분석";
  return mode;
}
