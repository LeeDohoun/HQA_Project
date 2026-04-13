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
          <span className="hero-badge">새 계정 만들기</span>
          <h1>처음부터 HQA 투자 프로필을 설정하세요.</h1>
          <p>
            회원가입 직후 투자 성향 입력 화면으로 이동하여,
            백엔드가 필요한 최소 사용자 정보를 바로 채울 수 있습니다.
          </p>
        </section>
        <section className="auth-form">
          <h2>회원가입</h2>
          <form className="stack" onSubmit={onSubmit}>
            <div className="form-grid two">
              <div className="field">
                <label htmlFor="userId">아이디</label>
                <input
                  id="userId"
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
                  value={form.firstName}
                  onChange={(event) => setForm((prev) => ({ ...prev, firstName: event.target.value }))}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="lastName">성</label>
                <input
                  id="lastName"
                  value={form.lastName}
                  onChange={(event) => setForm((prev) => ({ ...prev, lastName: event.target.value }))}
                  required
                />
              </div>
            </div>
            {error ? <p className="error-text">{error}</p> : null}
            <div className="button-row">
              <button className="button" disabled={loading} type="submit">
                {loading ? "생성 중..." : "계정 만들기"}
              </button>
              <Link className="button-ghost" href="/login">
                로그인으로 돌아가기
              </Link>
            </div>
          </form>
        </section>
      </div>
    </div>
  );
}
