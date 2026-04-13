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
        if (!active) {
          return;
        }
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
          if (!active) {
            return;
          }
          setProgress(JSON.parse((event as MessageEvent<string>).data) as AnalysisProgressEvent);
        });

        eventSource.addEventListener("completed", async () => {
          const latest = await analysisApi.result(taskId);
          if (!active) {
            return;
          }
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
            if (active) {
              setLoading(false);
            }
          }
        };
      } catch {
        setLoading(false);
      }
    }

    loadResult().finally(() => {
      if (active && !eventSource) {
        setLoading(false);
      }
    });

    return () => {
      active = false;
      eventSource?.close();
    };
  }, [taskId]);

  const statusTone = useMemo(() => {
    if (result?.status === "completed") {
      return "good";
    }
    if (result?.status === "failed") {
      return "bad";
    }
    return "warn";
  }, [result?.status]);

  return (
    <AppShell
      title="분석 결과"
      actions={<Link className="button-ghost" href="/dashboard">대시보드</Link>}
    >
      {error ? <p className="error-text">{error}</p> : null}
      <div className="panel-grid">
        <section className="panel" style={{ gridColumn: "span 12" }}>
          <div className="button-row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <h2 className="section-title" style={{ marginBottom: 4 }}>{result?.stock?.name ?? "로딩 중..."}</h2>
              <p className="meta mono">{result?.stock?.code ?? taskId}</p>
            </div>
            <StatusPill label={translateStatus(result?.status ?? "running")} tone={statusTone as "good" | "warn" | "bad"} />
          </div>
          {progress ? (
            <div className="stack" style={{ marginTop: 18 }}>
              <div className="button-row" style={{ justifyContent: "space-between" }}>
                <span className="meta">{progress.message}</span>
                <span className="meta">{Math.round(progress.progress * 100)}%</span>
              </div>
              <div className="progress-bar">
                <span style={{ width: `${Math.round(progress.progress * 100)}%` }} />
              </div>
            </div>
          ) : null}
          <div className="stat-strip" style={{ marginTop: 18 }}>
            <div className="stat"><span className="meta">모드</span><strong>{translateMode(result?.mode) ?? "-"}</strong></div>
            <div className="stat"><span className="meta">생성</span><strong style={{ fontSize: "1rem" }}>{formatDate(result?.createdAt)}</strong></div>
            <div className="stat"><span className="meta">완료</span><strong style={{ fontSize: "1rem" }}>{formatDate(result?.completedAt)}</strong></div>
          </div>
        </section>
        <section className="panel" style={{ gridColumn: "span 7" }}>
          <h2 className="section-title">점수</h2>
          {result?.scores?.length ? (
            <div className="score-list">
              {result.scores.map((score) => (
                <div className="score-card" key={score.agent}>
                  <div className="button-row" style={{ justifyContent: "space-between" }}>
                    <strong>{titleCaseAgent(score.agent)}</strong>
                    <StatusPill label={score.grade ?? "-"} tone="neutral" />
                  </div>
                  <p className="meta">{score.totalScore} / {score.maxScore}</p>
                  {score.opinion ? <p>{score.opinion}</p> : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state"><strong>점수 없음</strong></div>
          )}
        </section>
        <section className="panel" style={{ gridColumn: "span 5" }}>
          <h2 className="section-title">최종 판단</h2>
          {result?.finalDecision && Object.keys(result.finalDecision).length > 0 ? (
            <div className="detail-list">
              {Object.entries(result.finalDecision).map(([key, value]) => (
                <div className="detail-item" key={key}>
                  <span className="meta">{key}</span>
                  <div>{String(value)}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state"><strong>판단 없음</strong></div>
          )}
        </section>
        <section className="panel" style={{ gridColumn: "span 6" }}>
          <h2 className="section-title">경고</h2>
          {result?.qualityWarnings?.length ? (
            <div className="detail-list">
              {result.qualityWarnings.map((warning) => <div className="detail-item" key={warning}>{warning}</div>)}
            </div>
          ) : (
            <p className="meta">-</p>
          )}
        </section>
        <section className="panel" style={{ gridColumn: "span 6" }}>
          <h2 className="section-title">오류</h2>
          {result?.errors && Object.keys(result.errors).length > 0 ? (
            <div className="detail-list">
              {Object.entries(result.errors).map(([key, value]) => (
                <div className="detail-item" key={key}>
                  <span className="meta">{key}</span>
                  <div>{value}</div>
                </div>
              ))}
            </div>
          ) : (
            <p className="meta">{loading ? "로딩 중..." : "-"}</p>
          )}
        </section>
      </div>
    </AppShell>
  );
}

function translateStatus(status: string) {
  switch (status) {
    case "pending":
      return "대기 중";
    case "running":
      return "진행 중";
    case "completed":
      return "완료";
    case "failed":
      return "실패";
    default:
      return status;
  }
}

function translateMode(mode?: string | null) {
  if (mode === "full") {
    return "전체 분석";
  }
  if (mode === "quick") {
    return "빠른 분석";
  }
  return mode;
}
