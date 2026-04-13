"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { AppShell } from "@/components/common/app-shell";
import { StatusPill } from "@/components/common/status-pill";
import { analysisApi, authApi, stockApi } from "@/lib/api";
import type { AnalysisMode, AnalysisTaskResponse, AuthUser, StockSearchResult } from "@/types/api";

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([]);
  const [selected, setSelected] = useState<StockSearchResult | null>(null);
  const [mode, setMode] = useState<AnalysisMode>("full");
  const [message, setMessage] = useState("");
  const [loadingUser, setLoadingUser] = useState(true);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<AnalysisTaskResponse | null>(null);

  useEffect(() => {
    let active = true;
    authApi
      .me()
      .then((responseUser) => {
        if (!active) {
          return;
        }
        setUser(responseUser);
        if (!responseUser.surveyCompleted) {
          router.replace("/onboarding/preference");
        }
      })
      .catch(() => router.replace("/login"))
      .finally(() => {
        if (active) {
          setLoadingUser(false);
        }
      });

    return () => {
      active = false;
    };
  }, [router]);

  async function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!searchQuery.trim()) {
      return;
    }
    setSearching(true);
    setMessage("");
    try {
      const response = await stockApi.search(searchQuery.trim());
      setSearchResults(response.results);
      if (response.results.length === 0) {
        setSelected(null);
        setMessage("일치하는 종목을 찾지 못했습니다.");
      }
    } catch (searchError) {
      setMessage(searchError instanceof Error ? searchError.message : "종목 검색에 실패했습니다.");
    } finally {
      setSearching(false);
    }
  }

  async function submitAnalysis() {
    if (!selected) {
      setMessage("분석을 시작하기 전에 종목을 선택해주세요.");
      return;
    }
    setSubmitting(true);
    setMessage("");
    try {
      const response = await analysisApi.submit({
        stockName: selected.name,
        stockCode: selected.code,
        mode,
        maxRetries: mode === "full" ? 1 : 0
      });
      setTask(response);
      router.push(`/analysis/${response.taskId}`);
    } catch (submitError) {
      setMessage(submitError instanceof Error ? submitError.message : "분석 요청에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  async function logout() {
    await authApi.logout();
    router.push("/login");
  }

  if (loadingUser) {
    return (
      <div className="auth-wrap">
        <div className="card">
          <p className="meta">대시보드를 불러오는 중입니다...</p>
        </div>
      </div>
    );
  }

  return (
    <AppShell
      title="분석 대시보드"
      subtitle="종목을 검색하고 분석 모드를 선택한 뒤, AI 분석 진행 상황과 최종 결과를 확인하세요."
      actions={
        <>
          <Link className="button-ghost" href="/onboarding/preference">
            투자 성향 수정
          </Link>
          <button className="button-secondary" onClick={logout} type="button">
            로그아웃
          </button>
        </>
      }
    >
      <div className="panel-grid">
        <section className="panel" style={{ gridColumn: "span 5" }}>
          <h2 className="section-title">프로필 상태</h2>
          <p className="section-subtitle">세션 상태와 분석 준비 여부를 확인합니다.</p>
          <div className="stat-strip">
            <div className="stat">
              <span className="meta">로그인 사용자</span>
              <strong>{user?.firstName} {user?.lastName}</strong>
              <span className="meta mono">{user?.userId}</span>
            </div>
            <div className="stat">
              <span className="meta">투자 성향</span>
              <strong>{user?.surveyCompleted ? "완료" : "미입력"}</strong>
            </div>
            <div className="stat">
              <span className="meta">KIS 인증 정보</span>
              <strong>{user?.kisConfigured ? "설정됨" : "선택 사항"}</strong>
            </div>
          </div>
        </section>

        <section className="panel" style={{ gridColumn: "span 7" }}>
          <h2 className="section-title">종목 검색</h2>
          <form className="stack" onSubmit={onSearch}>
            <div className="button-row">
              <input
                placeholder="종목명 또는 종목코드로 검색하세요. 예: 삼성전자 또는 005930"
                style={{ flex: 1, minWidth: 260 }}
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
              />
              <button className="button" disabled={searching} type="submit">
                {searching ? "검색 중..." : "검색"}
              </button>
            </div>
          </form>
          <div className="search-results" style={{ marginTop: 16 }}>
            {searchResults.map((item) => (
              <button key={`${item.code}-${item.market}`} className="search-item" type="button" onClick={() => setSelected(item)}>
                <div style={{ textAlign: "left" }}>
                  <strong>{item.name}</strong>
                  <div className="meta mono">{item.code} · {item.market}</div>
                </div>
                <StatusPill label={selected?.code === item.code ? "선택됨" : "선택"} tone={selected?.code === item.code ? "good" : "neutral"} />
              </button>
            ))}
          </div>
        </section>

        <section className="panel" style={{ gridColumn: "span 12" }}>
          <h2 className="section-title">분석 시작</h2>
          <div className="analysis-layout">
            <div className="stack">
              <div className="detail-item">
                <span className="meta">선택한 종목</span>
                <h3 style={{ marginBottom: 6 }}>{selected?.name ?? "아직 선택된 종목이 없습니다"}</h3>
                <p className="meta mono">{selected?.code ?? "-"}</p>
              </div>
              <fieldset className="fieldset">
                <legend>분석 모드</legend>
                <div className="toggle-row">
                  <label className="toggle">
                    <input checked={mode === "full"} name="mode" type="radio" onChange={() => setMode("full")} />
                    전체 분석
                  </label>
                  <label className="toggle">
                    <input checked={mode === "quick"} name="mode" type="radio" onChange={() => setMode("quick")} />
                    빠른 분석
                  </label>
                </div>
              </fieldset>
            </div>
            <div className="stack">
              <div className="detail-item">
                <span className="meta">백엔드 동작 방식</span>
                <p className="meta">
                  전체 분석은 더 긴 분석 경로를 사용하고, 빠른 분석은 더 짧은 경로와
                  낮은 재시도 횟수를 사용합니다.
                </p>
              </div>
              {message ? <p className="error-text">{message}</p> : null}
              {task ? <p className="success-text">최근 생성된 작업: <span className="mono">{task.taskId}</span></p> : null}
              <div className="button-row">
                <button className="button" disabled={!selected || submitting} onClick={submitAnalysis} type="button">
                  {submitting ? "요청 중..." : "분석 시작"}
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
