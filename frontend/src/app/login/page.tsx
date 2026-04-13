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
          <span className="hero-badge">투자 분석 시작</span>
          <h1>로그인하고 국내 주식 분석을 시작하세요.</h1>
          <p>
            이 프런트엔드는 현재 Spring Boot 백엔드와 연결되어 있으며,
            세션 인증, 투자 성향 입력, 종목 검색, AI 분석 진행 조회를 지원합니다.
          </p>
        </section>
        <section className="auth-form">
          <h2>다시 오신 것을 환영합니다</h2>
          <form className="stack" onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="userId">아이디</label>
              <input id="userId" value={userId} onChange={(event) => setUserId(event.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor="password">비밀번호</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </div>
            {error ? <p className="error-text">{error}</p> : null}
            <div className="button-row">
              <button className="button" disabled={loading} type="submit">
                {loading ? "로그인 중..." : "로그인"}
              </button>
              <Link className="button-ghost" href="/signup">
                회원가입
              </Link>
            </div>
          </form>
        </section>
      </div>
    </div>
  );
}
