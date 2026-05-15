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
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (form.password !== confirmPassword) {
      setError("비밀번호가 일치하지 않아요.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await authApi.signup(form);
      router.push("/onboarding/preference");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "회원가입에 실패했어요.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="auth-brand-mark">H</span>
          <span>HQA</span>
        </div>

        <div>
          <h1 className="auth-title">계정 하나면<br />AI가 함께해요 ✨</h1>
          <p className="auth-sub">몇 가지만 입력하면 바로 시작할 수 있어요.</p>
        </div>

        <form className="auth-form" onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="userId">아이디</label>
            <input
              id="userId"
              placeholder="4자 이상으로 만들어주세요"
              minLength={4}
              value={form.userId}
              onChange={(event) => setForm((prev) => ({ ...prev, userId: event.target.value }))}
              required
            />
          </div>
          <div className="form-grid two">
            <div className="field">
              <label htmlFor="lastName">성</label>
              <input
                id="lastName"
                placeholder="홍"
                value={form.lastName}
                onChange={(event) => setForm((prev) => ({ ...prev, lastName: event.target.value }))}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="firstName">이름</label>
              <input
                id="firstName"
                placeholder="길동"
                value={form.firstName}
                onChange={(event) => setForm((prev) => ({ ...prev, firstName: event.target.value }))}
                required
              />
            </div>
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
          <div className="field">
            <label htmlFor="confirmPassword">비밀번호 확인</label>
            <input
              id="confirmPassword"
              placeholder="한 번 더 입력해주세요"
              minLength={8}
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              required
            />
          </div>
          {error ? <p className="error-text">{error}</p> : null}
          <button className="wiz-cta" disabled={loading} type="submit">
            {loading ? "만드는 중..." : "계정 만들기"}
          </button>
        </form>

        <p className="auth-foot">
          이미 계정이 있으신가요? <Link href="/login">로그인</Link>
        </p>
      </div>
    </div>
  );
}
