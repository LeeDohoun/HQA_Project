"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";
import {
  investmentExperienceOptions,
  investmentGoalOptions,
  investmentTypeOptions,
  lossActionOptions,
  lossToleranceOptions,
  occupationTypeOptions,
  volatilityToleranceOptions
} from "@/lib/options";
import type { KisCredentials, UserPreference } from "@/types/api";

const defaultPreference: UserPreference = {
  totalAssets: 0,
  monthlyInvestment: 0,
  investmentPeriodMonths: 12,
  targetReturnRate: 8,
  investmentGoal: "ASSET_GROWTH",
  investmentExperience: "BEGINNER",
  birthDate: "1995-01-01",
  investmentType: "NEUTRAL",
  volatilityTolerance: "MEDIUM",
  lossAction: "HOLD",
  leverageAllowed: false,
  occupationType: "EMPLOYEE",
  lossTolerance: "LEVEL_2"
};

// Curated option lists with friendly icons + sublabels.
// We pick a smaller subset for the most common picks and keep the full list
// reachable through "더보기" only where it matters.
const goalChoices: { value: string; label: string; emoji: string; sub: string }[] = [
  { value: "ASSET_GROWTH",   label: "자산을 불리고 싶어요",   emoji: "📈", sub: "장기적으로 돈을 키우는 게 목표" },
  { value: "RETIREMENT",     label: "은퇴를 준비 중이에요",   emoji: "🌿", sub: "안정적인 노후 자금" },
  { value: "HOME_PURCHASE",  label: "내 집 마련",           emoji: "🏠", sub: "주택 구매를 위한 자금" },
  { value: "PASSIVE_INCOME", label: "월급 외 수익이 필요해요", emoji: "💰", sub: "꾸준한 현금 흐름" },
  { value: "EDUCATION_FUND", label: "교육 자금",            emoji: "📚", sub: "학자금·자녀 교육" },
  { value: "OTHER",          label: "다른 목표예요",         emoji: "✨", sub: "" }
];

const experienceChoices: { value: string; label: string; emoji: string; sub: string }[] = [
  { value: "NONE",         label: "처음이에요",         emoji: "🌱", sub: "투자 경험이 없어요" },
  { value: "BEGINNER",     label: "1년 미만",          emoji: "🌿", sub: "이제 막 시작했어요" },
  { value: "INTERMEDIATE", label: "1~3년 정도",        emoji: "🌳", sub: "어느 정도 익숙해요" },
  { value: "EXPERIENCED",  label: "3~5년 정도",        emoji: "🎯", sub: "꽤 해봤어요" },
  { value: "EXPERT",       label: "5년 이상",          emoji: "🏆", sub: "전문가 수준이에요" }
];

const styleChoices: { value: string; label: string; emoji: string; sub: string }[] = [
  { value: "STABLE",          label: "안정형",        emoji: "🛡️", sub: "잃지 않는 게 가장 중요해요" },
  { value: "MID_STABLE",      label: "안정 추구형",    emoji: "🧭", sub: "조금만 위험을 감수할게요" },
  { value: "NEUTRAL",         label: "균형형",        emoji: "⚖️", sub: "수익과 안정의 균형" },
  { value: "MID_AGGRESSIVE",  label: "성장 추구형",    emoji: "🚀", sub: "수익을 더 노릴게요" },
  { value: "AGGRESSIVE",      label: "공격형",        emoji: "🔥", sub: "큰 수익을 위해서라면" }
];

const lossActionChoices: { value: string; label: string; emoji: string; sub: string }[] = [
  { value: "HOLD",              label: "그대로 두고 기다려요",  emoji: "🧘", sub: "장기적으로 회복될 거라 믿어요" },
  { value: "BUY_MORE",          label: "오히려 더 사요",       emoji: "💪", sub: "싸졌으니까 기회예요" },
  { value: "SEEK_ADVICE",       label: "전문가에게 물어봐요",   emoji: "🤝", sub: "AI 분석으로 다시 확인" },
  { value: "SELL_IMMEDIATELY",  label: "바로 정리해요",        emoji: "✋", sub: "손실은 빠르게 끊어요" }
];

const lossToleranceChoices = lossToleranceOptions.map(([value, label], i) => ({
  value,
  label,
  emoji: ["😌", "🙂", "😐", "😟", "😨", "😱"][i] ?? "📊",
  sub: ""
}));

type StepKey = "welcome" | "name" | "goal" | "experience" | "style" | "loss" | "money" | "kis" | "review";

const STEPS: StepKey[] = ["welcome", "goal", "experience", "style", "loss", "money", "kis", "review"];

const emptyKis: KisCredentials = {
  kisAppKey: "",
  kisAppSecret: "",
  kisAccountNo: "",
  kisAccountProductCode: "01",
  kisIsReal: false
};

export default function PreferencePage() {
  const router = useRouter();
  const [form, setForm] = useState<UserPreference>(defaultPreference);
  const [kis, setKis] = useState<KisCredentials>(emptyKis);
  const [stepIdx, setStepIdx] = useState(0);
  const [direction, setDirection] = useState<"forward" | "back">("forward");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    authApi
      .getPreference()
      .then((preference) => {
        if (!active) return;
        setForm((prev) => ({ ...prev, ...preference, birthDate: preference.birthDate.slice(0, 10) }));
      })
      .catch(() => {
        // First-time users won't have a preference yet.
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const step = STEPS[stepIdx];
  const totalSteps = STEPS.length;
  const progress = ((stepIdx + 1) / totalSteps) * 100;

  function go(delta: 1 | -1) {
    setDirection(delta === 1 ? "forward" : "back");
    setStepIdx((idx) => Math.min(Math.max(idx + delta, 0), totalSteps - 1));
  }

  function pick<K extends keyof UserPreference>(key: K, value: UserPreference[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    // Auto-advance after a short beat so the selection animation can play.
    window.setTimeout(() => go(1), 240);
  }

  async function submit() {
    setSaving(true);
    setError("");
    try {
      await authApi.savePreference(form);
      const hasKis =
        kis.kisAppKey.trim() &&
        kis.kisAppSecret.trim() &&
        kis.kisAccountNo.trim() &&
        kis.kisAccountProductCode.trim();
      if (hasKis) {
        await authApi.saveKis(kis);
      }
      router.push("/dashboard");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "저장에 실패했습니다.");
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="wiz-wrap">
        <div className="wiz-card" style={{ marginTop: 40, textAlign: "center" }}>
          <p className="meta">불러오는 중...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="wiz-wrap">
      <div className="wiz-top">
        <button
          type="button"
          className="wiz-back"
          aria-label="뒤로"
          onClick={() => go(-1)}
          disabled={stepIdx === 0}
          style={{ opacity: stepIdx === 0 ? 0.4 : 1 }}
        >
          ←
        </button>
        <div className="wiz-progress-track" aria-hidden>
          <div className="wiz-progress-fill" style={{ width: `${progress}%` }} />
        </div>
        <span className="wiz-step-count">{stepIdx + 1} / {totalSteps}</span>
      </div>

      {/* re-key on step so the card replays its entrance animation */}
      <section key={step} className="wiz-card" data-direction={direction}>
        {step === "welcome" && <Welcome onNext={() => go(1)} />}

        {step === "goal" && (
          <ChoiceStep
            emoji="🎯"
            question="투자로 이루고 싶은 게 뭐예요?"
            hint="가장 큰 목표 하나만 골라주세요."
            choices={goalChoices}
            value={form.investmentGoal}
            onPick={(v) => pick("investmentGoal", v)}
          />
        )}

        {step === "experience" && (
          <ChoiceStep
            emoji="📚"
            question="투자, 얼마나 해보셨어요?"
            hint="솔직하게 골라주셔도 괜찮아요."
            choices={experienceChoices}
            value={form.investmentExperience}
            onPick={(v) => pick("investmentExperience", v)}
          />
        )}

        {step === "style" && (
          <ChoiceStep
            emoji="🎨"
            question="어떤 스타일로 투자하고 싶어요?"
            hint="수익을 더 노릴지, 안전을 더 챙길지 정해볼게요."
            choices={styleChoices}
            value={form.investmentType}
            onPick={(v) => {
              setForm((prev) => ({ ...prev, investmentType: v, volatilityTolerance: mapVolatility(v) }));
              window.setTimeout(() => go(1), 240);
            }}
          />
        )}

        {step === "loss" && (
          <ChoiceStep
            emoji="📉"
            question="손실이 생기면, 어떻게 대응하실래요?"
            hint="가장 마음에 가까운 걸 골라주세요."
            choices={lossActionChoices}
            value={form.lossAction}
            onPick={(v) => {
              setForm((prev) => ({ ...prev, lossAction: v }));
              window.setTimeout(() => go(1), 240);
            }}
          />
        )}

        {step === "money" && (
          <MoneyStep
            form={form}
            onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
            onNext={() => go(1)}
          />
        )}

        {step === "kis" && (
          <KisStep
            value={kis}
            onChange={(patch) => setKis((prev) => ({ ...prev, ...patch }))}
            onNext={() => go(1)}
            onSkip={() => {
              setKis(emptyKis);
              go(1);
            }}
          />
        )}

        {step === "review" && (
          <Review
            form={form}
            saving={saving}
            error={error}
            onSubmit={submit}
          />
        )}
      </section>
    </div>
  );
}

/* ────────────────────────────────────────── */

function Welcome({ onNext }: { onNext: () => void }) {
  return (
    <>
      <span className="wiz-emoji wave" aria-hidden>👋</span>
      <h1 className="wiz-question">반가워요!<br />몇 가지만 알려주세요.</h1>
      <p className="wiz-hint">
        AI가 더 잘 추천해드릴 수 있도록<br />
        투자 스타일을 1분만 알려주시면 돼요.
      </p>
      <button type="button" className="wiz-cta pulse" onClick={onNext}>
        시작하기 →
      </button>
    </>
  );
}

function ChoiceStep({
  emoji,
  question,
  hint,
  choices,
  value,
  onPick
}: {
  emoji: string;
  question: string;
  hint?: string;
  choices: { value: string; label: string; emoji: string; sub: string }[];
  value: string;
  onPick: (value: string) => void;
}) {
  return (
    <>
      <span className="wiz-emoji" aria-hidden>{emoji}</span>
      <h1 className="wiz-question">{question}</h1>
      {hint ? <p className="wiz-hint">{hint}</p> : null}
      <div className="wiz-options" data-stagger>
        {choices.map((c, i) => (
          <button
            key={c.value}
            type="button"
            className={`wiz-option ${value === c.value ? "selected" : ""}`}
            onClick={() => onPick(c.value)}
            style={{ "--i": i } as React.CSSProperties}
          >
            <span className="wiz-option-icon" aria-hidden>{c.emoji}</span>
            <span className="wiz-option-body">
              <span>{c.label}</span>
              {c.sub ? <span className="wiz-option-sub">{c.sub}</span> : null}
            </span>
          </button>
        ))}
      </div>
    </>
  );
}

function MoneyStep({
  form,
  onChange,
  onNext
}: {
  form: UserPreference;
  onChange: (patch: Partial<UserPreference>) => void;
  onNext: () => void;
}) {
  const valid = form.totalAssets >= 0 && form.monthlyInvestment >= 0 && form.investmentPeriodMonths >= 1;
  return (
    <>
      <span className="wiz-emoji" aria-hidden>💵</span>
      <h1 className="wiz-question">투자 금액을 알려주세요</h1>
      <p className="wiz-hint">대략적인 숫자도 괜찮아요. 언제든 바꿀 수 있어요.</p>

      <div className="field">
        <label>현재 보유 자산</label>
        <div className="wiz-input-suffix">
          <input
            className="wiz-input"
            type="number"
            min={0}
            inputMode="numeric"
            value={form.totalAssets || ""}
            placeholder="0"
            onChange={(e) => onChange({ totalAssets: Number(e.target.value) || 0 })}
          />
          <span>원</span>
        </div>
      </div>

      <div className="field">
        <label>매달 투자할 금액</label>
        <div className="wiz-input-suffix">
          <input
            className="wiz-input"
            type="number"
            min={0}
            inputMode="numeric"
            value={form.monthlyInvestment || ""}
            placeholder="0"
            onChange={(e) => onChange({ monthlyInvestment: Number(e.target.value) || 0 })}
          />
          <span>원/월</span>
        </div>
      </div>

      <div className="field">
        <label>투자 기간</label>
        <div className="wiz-input-suffix">
          <input
            className="wiz-input"
            type="number"
            min={1}
            max={600}
            inputMode="numeric"
            value={form.investmentPeriodMonths}
            onChange={(e) => onChange({ investmentPeriodMonths: Number(e.target.value) || 1 })}
          />
          <span>개월</span>
        </div>
      </div>

      <button type="button" className="wiz-cta" disabled={!valid} onClick={onNext}>
        다음
      </button>
    </>
  );
}

function KisStep({
  value,
  onChange,
  onNext,
  onSkip
}: {
  value: KisCredentials;
  onChange: (patch: Partial<KisCredentials>) => void;
  onNext: () => void;
  onSkip: () => void;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState("");
  const valid =
    value.kisAppKey.trim().length > 0 &&
    value.kisAppSecret.trim().length > 0 &&
    value.kisAccountNo.trim().length > 0 &&
    value.kisAccountProductCode.trim().length > 0;

  async function handleVerifyAndNext() {
    setVerifying(true);
    setVerifyError("");
    try {
      const result = await authApi.verifyKis(value);
      if (result.ok) {
        onNext();
      } else {
        setVerifyError(result.message || "연결에 실패했어요. 입력값을 다시 확인해주세요.");
      }
    } catch (e) {
      setVerifyError(e instanceof Error ? e.message : "검증 요청에 실패했어요.");
    } finally {
      setVerifying(false);
    }
  }

  return (
    <>
      <span className="wiz-emoji" aria-hidden>🔑</span>
      <h1 className="wiz-question">증권사 키를 연결해주세요</h1>
      <p className="wiz-hint">
        AI 분석 결과로 <b>실제 매수·자동매매</b>를 하려면 한국투자증권(KIS) API 키가 필요해요.
        지금 안 넣어도 둘러보기는 가능해요.
      </p>

      {/* 실전 / 모의 환경 토글 */}
      <div className="field">
        <label>투자 환경</label>
        <div className="env-toggle" role="tablist" aria-label="투자 환경">
          <button
            type="button"
            role="tab"
            aria-selected={!value.kisIsReal}
            className={`env-toggle-btn ${!value.kisIsReal ? "active sandbox" : ""}`}
            onClick={() => onChange({ kisIsReal: false })}
          >
            모의투자
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={value.kisIsReal}
            className={`env-toggle-btn ${value.kisIsReal ? "active real" : ""}`}
            onClick={() => onChange({ kisIsReal: true })}
          >
            실전투자
          </button>
        </div>
        <p className="field-hint">
          {value.kisIsReal
            ? "⚠️ 실제 자금이 사용돼요. 발급받은 키가 실전용인지 확인해주세요."
            : "안전한 모의투자로 먼저 테스트해볼 수 있어요."}
        </p>
      </div>

      <button
        type="button"
        className="wiz-disclosure"
        aria-expanded={showDetail}
        onClick={() => setShowDetail((v) => !v)}
      >
        {showDetail ? "▾ 자세히 닫기" : "▸ 왜 필요한지 / 안전한지 자세히 보기"}
      </button>

      {showDetail ? (
        <div className="wiz-explainer">
          <div className="wiz-explainer-row">
            <span className="wiz-explainer-icon" aria-hidden>📈</span>
            <div>
              <p className="wiz-explainer-title">왜 필요한가요?</p>
              <p className="wiz-explainer-text">
                HQA는 직접 주식을 보관하지 않아요. 키를 통해 <b>당신의 증권 계좌</b>에 매수 주문을 대신 넣어주는 구조예요.
                키가 없으면 AI 분석·차트 조회까지만 사용 가능하고, 실거래/자동매매는 비활성화돼요.
              </p>
            </div>
          </div>
          <div className="wiz-explainer-row">
            <span className="wiz-explainer-icon" aria-hidden>🔒</span>
            <div>
              <p className="wiz-explainer-title">안전한가요?</p>
              <p className="wiz-explainer-text">
                키는 AES-256 암호화로 저장되고, 매번 주문 직전에만 복호화돼요.
                서버 직원·DB 백업·로그 어디에서도 평문은 보이지 않아요.
              </p>
            </div>
          </div>
          <div className="wiz-explainer-row">
            <span className="wiz-explainer-icon" aria-hidden>🛂</span>
            <div>
              <p className="wiz-explainer-title">언제든 끊을 수 있나요?</p>
              <p className="wiz-explainer-text">
                네. 설정에서 키를 지우면 즉시 모든 거래 연결이 차단돼요.
                키는 <b>매수 권한만</b>이고, 출금이나 송금은 불가능해요.
              </p>
            </div>
          </div>
          <p className="wiz-explainer-link">
            발급 방법은{" "}
            <a href="https://apiportal.koreainvestment.com/" target="_blank" rel="noopener noreferrer">
              한국투자증권 OpenAPI 포털 ↗
            </a>
            에서 확인할 수 있어요.
          </p>
        </div>
      ) : null}

      <div className="field">
        <label>App Key</label>
        <input
          className="wiz-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={value.kisAppKey}
          placeholder="PSxxxxxxxxxxxxxxxxxx"
          onChange={(e) => onChange({ kisAppKey: e.target.value })}
        />
      </div>

      <div className="field">
        <label>App Secret</label>
        <input
          className="wiz-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={value.kisAppSecret}
          placeholder="발급받은 시크릿 키"
          onChange={(e) => onChange({ kisAppSecret: e.target.value })}
        />
      </div>

      <div className="field">
        <label>계좌번호 (CANO)</label>
        <input
          className="wiz-input"
          type="password"
          inputMode="numeric"
          autoComplete="off"
          value={value.kisAccountNo}
          placeholder="12345678"
          onChange={(e) => onChange({ kisAccountNo: e.target.value })}
        />
      </div>

      <div className="field">
        <label>계좌상품코드 (ACNT_PRDT_CD)</label>
        <input
          className="wiz-input"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          value={value.kisAccountProductCode}
          placeholder="01"
          onChange={(e) => onChange({ kisAccountProductCode: e.target.value })}
        />
      </div>

      {verifyError ? (
        <p className="error-text" role="alert">{verifyError}</p>
      ) : null}

      <button
        type="button"
        className="wiz-cta"
        disabled={!valid || verifying}
        onClick={handleVerifyAndNext}
      >
        {verifying ? "KIS 서버에 확인 중..." : "연결 확인하고 다음"}
      </button>
      <button type="button" className="wiz-skip" onClick={onSkip} disabled={verifying}>
        나중에 설정에서 입력할게요
      </button>
    </>
  );
}

function Review({
  form,
  saving,
  error,
  onSubmit
}: {
  form: UserPreference;
  saving: boolean;
  error: string;
  onSubmit: () => void;
}) {
  const goal = useMemo(() => labelOf(investmentGoalOptions, form.investmentGoal), [form.investmentGoal]);
  const xp = useMemo(() => labelOf(investmentExperienceOptions, form.investmentExperience), [form.investmentExperience]);
  const style = useMemo(() => labelOf(investmentTypeOptions, form.investmentType), [form.investmentType]);
  const loss = useMemo(() => labelOf(lossActionOptions, form.lossAction), [form.lossAction]);

  return (
    <div className="wiz-success">
      <span className="wiz-success-mark" aria-hidden>✓</span>
      <h1 className="wiz-question">거의 다 됐어요!</h1>
      <p className="wiz-hint" style={{ marginTop: -4 }}>이대로 시작해볼까요?</p>

      <div className="wiz-summary">
        <div className="wiz-summary-row"><span>목표</span><span>{goal}</span></div>
        <div className="wiz-summary-row"><span>경험</span><span>{xp}</span></div>
        <div className="wiz-summary-row"><span>스타일</span><span>{style}</span></div>
        <div className="wiz-summary-row"><span>손실 대응</span><span>{loss}</span></div>
        <div className="wiz-summary-row">
          <span>월 투자</span>
          <span>{form.monthlyInvestment.toLocaleString("ko-KR")}원</span>
        </div>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <button type="button" className="wiz-cta" disabled={saving} onClick={onSubmit}>
        {saving ? "저장 중..." : "시작하기 🚀"}
      </button>
    </div>
  );
}

/* ────────────────────────────────────────── */

function labelOf(options: readonly (readonly [string, string])[], value: string) {
  return options.find(([v]) => v === value)?.[1] ?? value;
}

function mapVolatility(style: string): UserPreference["volatilityTolerance"] {
  switch (style) {
    case "STABLE":         return "VERY_LOW";
    case "MID_STABLE":     return "LOW";
    case "NEUTRAL":        return "MEDIUM";
    case "MID_AGGRESSIVE": return "HIGH";
    case "AGGRESSIVE":     return "VERY_HIGH";
    default:               return "MEDIUM";
  }
}

// Silence unused-import lint while keeping the option list available for future steps.
void volatilityToleranceOptions;
void lossToleranceChoices;
void occupationTypeOptions;
