"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { authApi } from "@/lib/api";

const LOGIN_CSS = `
.login-page {
  --paper:#14130d; --paper-2:#1d1b12; --ink:#ece6d3; --ink-2:#a39c84; --ink-3:#6d6753;
  --card:#1f1c12; --moss:#36b079; --moss-2:#43c489; --spark:#e0a341; --rule:#322d1f;
  --serif:Georgia,"Times New Roman",serif;
  --sans:-apple-system,BlinkMacSystemFont,"Pretendard","Apple SD Gothic Neo","Noto Sans KR","Segoe UI",sans-serif;
  min-height:100vh;
  background:var(--paper);
  color:var(--ink);
  font-family:var(--sans);
  display:grid;
  grid-template-columns:minmax(0,1fr) minmax(360px,460px);
}
.login-page *{box-sizing:border-box;}
.login-page__story{
  min-height:100vh;
  padding:clamp(36px,6vw,76px);
  display:flex;
  flex-direction:column;
  justify-content:space-between;
  border-right:1px solid var(--rule);
  background:
    linear-gradient(rgba(20,19,13,.58), rgba(20,19,13,.82)),
    url("https://images.unsplash.com/photo-1642790106117-e829e14a795f?auto=format&fit=crop&w=1600&q=80");
  background-size:cover;
  background-position:center;
}
.login-page__mark{display:inline-flex; align-items:baseline; width:max-content;}
.login-page__mark b{font-family:var(--serif); font-style:italic; font-size:1.8rem; letter-spacing:-.02em;}
.login-page__mark i{width:7px; height:7px; border-radius:50%; background:var(--moss); margin-left:4px; margin-bottom:6px;}
.login-page__copy{max-width:720px;}
.login-page__kicker{font-family:var(--serif); font-style:italic; color:var(--spark); font-size:1.08rem; margin:0 0 10px;}
.login-page__title{font-size:clamp(2.4rem,5.2vw,5.6rem); line-height:.96; letter-spacing:-.04em; margin:0; font-weight:800;}
.login-page__lede{max-width:36rem; margin:20px 0 0; color:rgba(236,230,211,.74); font-size:1.04rem; line-height:1.7;}
.login-page__metrics{display:flex; gap:34px; flex-wrap:wrap; margin-top:34px;}
.login-page__metric small{display:block; color:rgba(236,230,211,.58); font-size:.76rem; font-weight:800;}
.login-page__metric b{display:block; font-family:var(--serif); color:var(--ink); font-size:1.42rem; margin-top:4px;}
.login-page__panel{
  min-height:100vh;
  display:flex;
  align-items:center;
  justify-content:center;
  padding:32px;
  background:var(--paper);
}
.login-page__card{width:100%; max-width:360px;}
.login-page__card h1{font-size:1.55rem; margin:0; letter-spacing:-.03em;}
.login-page__card p{margin:8px 0 0; color:var(--ink-2); font-size:.92rem;}
.login-page__form{display:grid; gap:14px; margin-top:26px;}
.login-page__field{display:grid; gap:7px;}
.login-page__field label{font-size:.76rem; font-weight:800; color:var(--ink-2);}
.login-page__field input{
  width:100%;
  border:1px solid var(--rule);
  border-radius:5px;
  background:var(--card);
  color:var(--ink);
  padding:12px 13px;
  outline:none;
}
.login-page__field input:focus{border-color:var(--moss);}
.login-page__field input::placeholder{color:var(--ink-3);}
.login-page__error{
  margin:0;
  padding:10px 12px;
  border-left:3px solid #d2554a;
  background:var(--card);
  color:#d2554a;
  font-size:.86rem;
}
.login-page__submit{
  width:100%;
  border:0;
  border-radius:5px;
  padding:13px 18px;
  background:var(--moss);
  color:#0b2417;
  font-weight:900;
}
.login-page__submit:hover:not(:disabled){background:var(--moss-2);}
.login-page__submit:disabled{opacity:.48; cursor:not-allowed;}
.login-page__hint{
  margin-top:18px;
  padding-top:18px;
  border-top:1px solid var(--rule);
  color:var(--ink-3);
  font-size:.82rem;
  line-height:1.7;
}
.login-page__hint code{color:var(--ink); font-family:var(--sans); font-weight:800;}
.login-page__foot{margin-top:18px; color:var(--ink-3); font-size:.86rem; text-align:center;}
.login-page__foot a{color:var(--moss); font-weight:800; text-decoration:underline; text-underline-offset:4px;}
@media (max-width:860px){
  .login-page{grid-template-columns:1fr;}
  .login-page__story{min-height:42vh; border-right:0; border-bottom:1px solid var(--rule);}
  .login-page__panel{min-height:auto; padding:30px 20px 48px;}
}
`;

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
      <style dangerouslySetInnerHTML={{ __html: LOGIN_CSS }} />
      <section className="login-page__story">
        <span className="login-page__mark" aria-label="HQA">
          <b>HQA</b>
          <i />
        </span>
        <div className="login-page__copy">
          <p className="login-page__kicker">AI Trading Workspace</p>
          <h1 className="login-page__title">분석과 매매를 한 곳에서.</h1>
          <p className="login-page__lede">
            관심 종목 분석, 계좌 상태, 자동매매 설정을 한 화면에서 관리합니다.
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
          <h1>로그인</h1>
          <p>가입한 계정 정보를 입력해 대시보드로 이동합니다.</p>

          <form className="login-page__form" onSubmit={onSubmit}>
          <div className="login-page__field">
            <label htmlFor="userId">아이디</label>
            <input
              id="userId"
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
              type="password"
              placeholder="비밀번호"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          {error ? <p className="login-page__error">{error}</p> : null}
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
