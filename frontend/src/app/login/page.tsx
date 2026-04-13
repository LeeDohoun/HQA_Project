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
          <span className="hero-badge">HQA</span>
          <h1>로그인</h1>
        </section>
        <section className="auth-form">
          <h2>계정 접속</h2>
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
