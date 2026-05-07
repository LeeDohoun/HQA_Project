"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { authApi } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await authApi.login({ userId, password });
      router.push(response.user?.surveyCompleted ? "/dashboard" : "/onboarding/preference");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "로그인에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <section className="auth-intro">
          <div className="auth-intro-top">
            <span className="hero-badge">HQA</span>
            <div>
              <h1>투자의 시작,<br />지금 시작하세요</h1>
              <p style={{ marginTop: 16 }}>AI 기반 주식 분석 플랫폼으로<br />더 스마트한 투자 결정을 내리세요.</p>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 32 }}>
            {["KOSPI", "KOSDAQ", "AI 분석"].map((tag) => (
              <span
                key={tag}
                style={{
                  padding: "4px 10px",
                  borderRadius: 6,
                  background: "rgba(69,137,255,0.12)",
                  color: "#4589ff",
                  fontSize: "0.75rem",
                  fontWeight: 600
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        </section>

        <section className="auth-form">
          <div>
            <h2>로그인</h2>
            <p style={{ margin: "6px 0 0", fontSize: "0.82rem", color: "var(--muted)" }}>
              계정이 없으신가요?{" "}
              <Link href="/signup" style={{ color: "var(--accent)", fontWeight: 600 }}>
                회원가입
              </Link>
            </p>
          </div>

          <form className="stack" onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="userId">아이디</label>
              <input
                id="userId"
                placeholder="아이디를 입력하세요"
                value={userId}
                onChange={(event) => setUserId(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="password">비밀번호</label>
              <input
                id="password"
                type="password"
                placeholder="비밀번호를 입력하세요"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </div>
            {error ? <p className="error-text">{error}</p> : null}
            <button className="button" disabled={loading} type="submit" style={{ marginTop: 4, minHeight: 42, justifyContent: "center" }}>
              {loading ? "로그인 중..." : "로그인"}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
