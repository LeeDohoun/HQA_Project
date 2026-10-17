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
    <div className="login-page">
      <section className="login-page__story">
        <Link href="/" className="login-page__mark" aria-label="HQA 홈">
          <b>HQA</b>
          <i />
        </Link>
        <div className="login-page__copy">
          <p className="login-page__kicker">AI Trading Workspace</p>
          <h1 className="login-page__title">나의 투자에,<br />새로운 시선.</h1>
          <p className="login-page__lede">
            복잡한 시장 속에서 나만의 기준을 찾는 곳. 오늘의 투자 이야기를 HQA와 이어가세요.
          </p>
          <div className="login-page__metrics">
            <span className="login-page__metric">
              <small>종목 분석</small>
              <b>AI 리서치</b>
            </span>
            <span className="login-page__metric">
              <small>계좌 연동</small>
              <b>KIS API</b>
            </span>
            <span className="login-page__metric">
              <small>매매 모드</small>
              <b>모의투자</b>
            </span>
          </div>
        </div>
      </section>

      <section className="login-page__panel">
        <div className="login-page__card">
          <h1>다시 만나 반가워요.</h1>
          <p>가입한 계정 정보를 입력해 대시보드로 이동합니다.</p>

          <form className="login-page__form" onSubmit={onSubmit}>
          <div className="login-page__field">
            <label htmlFor="userId">아이디</label>
            <input
              id="userId"
              autoComplete="username"
              placeholder="아이디"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              required
            />
          </div>
          <div className="login-page__field">
            <label htmlFor="password">비밀번호</label>
            <input
              id="password"
              autoComplete="current-password"
              type="password"
              placeholder="비밀번호"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          {error ? <p role="alert" className="login-page__error">{error}</p> : null}
          <button className="login-page__submit" disabled={loading} type="submit">
            {loading ? "로그인 중..." : "로그인"}
          </button>
        </form>

        <p className="login-page__foot">
          계정이 없으신가요? <Link href="/signup">회원가입</Link>
        </p>
      </div>
      </section>
    </div>
  );
}
