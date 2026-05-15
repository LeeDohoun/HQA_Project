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
import type { UserPreference } from "@/types/api";

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

type StepKey = "welcome" | "name" | "goal" | "experience" | "style" | "loss" | "money" | "review";

const STEPS: StepKey[] = ["welcome", "goal", "experience", "style", "loss", "money", "review"];

export default function PreferencePage() {
  const router = useRouter();
  const [form, setForm] = useState<UserPreference>(defaultPreference);
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
