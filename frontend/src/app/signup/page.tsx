"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { authApi } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const [form, setForm] = useState({
    userId: "",
    firstName: "",
    lastName: "",
    password: ""
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await authApi.signup(form);
      router.push("/onboarding/preference");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "회원가입에 실패했습니다.");
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
              <h1>함께 시작하는<br />스마트 투자</h1>
              <p style={{ marginTop: 16 }}>AI 에이전트가 실시간으로<br />종목을 분석해드립니다.</p>
            </div>
          </div>
          <div style={{ display: "grid", gap: 12, marginTop: 32 }}>
            {[
              { icon: "◎", text: "멀티 AI 에이전트 분석" },
              { icon: "◈", text: "실시간 주가 차트" },
              { icon: "◉", text: "맞춤형 투자 성향 분석" }
            ].map((item) => (
              <div key={item.text} style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span style={{ color: "var(--accent)", fontSize: "0.85rem" }}>{item.icon}</span>
                <span style={{ color: "var(--muted-2)", fontSize: "0.82rem" }}>{item.text}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="auth-form">
          <div>
            <h2>계정 만들기</h2>
            <p style={{ margin: "6px 0 0", fontSize: "0.82rem", color: "var(--muted)" }}>
              이미 계정이 있으신가요?{" "}
              <Link href="/login" style={{ color: "var(--accent)", fontWeight: 600 }}>
                로그인
              </Link>
            </p>
          </div>

          <form className="stack" onSubmit={onSubmit}>
            <div className="form-grid two">
              <div className="field">
                <label htmlFor="userId">아이디</label>
                <input
                  id="userId"
                  placeholder="4자 이상"
                  minLength={4}
                  value={form.userId}
                  onChange={(event) => setForm((prev) => ({ ...prev, userId: event.target.value }))}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="password">비밀번호</label>
                <input
                  id="password"
                  placeholder="8자 이상"
                  minLength={8}
                  type="password"
                  value={form.password}
                  onChange={(event) => setForm((prev) => ({ ...prev, password: event.target.value }))}
                  required
                />
              </div>
            </div>
            <div className="form-grid two">
              <div className="field">
                <label htmlFor="firstName">이름</label>
                <input
                  id="firstName"
                  placeholder="이름"
                  value={form.firstName}
                  onChange={(event) => setForm((prev) => ({ ...prev, firstName: event.target.value }))}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="lastName">성</label>
                <input
                  id="lastName"
                  placeholder="성"
                  value={form.lastName}
                  onChange={(event) => setForm((prev) => ({ ...prev, lastName: event.target.value }))}
                  required
                />
              </div>
            </div>
            {error ? <p className="error-text">{error}</p> : null}
            <button className="button" disabled={loading} type="submit" style={{ marginTop: 4, minHeight: 42, justifyContent: "center" }}>
              {loading ? "생성 중..." : "계정 만들기"}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
