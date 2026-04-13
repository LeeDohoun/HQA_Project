"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/common/app-shell";
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

export default function PreferencePage() {
  const router = useRouter();
  const [form, setForm] = useState<UserPreference>(defaultPreference);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    authApi
      .getPreference()
      .then((preference) => {
        if (active) {
          setForm((prev) => ({ ...prev, ...preference, birthDate: preference.birthDate.slice(0, 10) }));
        }
      })
      .catch(() => {
        // Missing preference is expected for first-time users.
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await authApi.savePreference(form);
      router.push("/dashboard");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "저장에 실패했습니다.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell title="투자 성향 설정">
      <div className="panel">
        {loading ? (
          <p className="meta">로딩 중...</p>
        ) : (
          <form className="stack" onSubmit={onSubmit}>
            <div className="form-grid two">
              <NumberField label="총 자산" value={form.totalAssets} onChange={(value) => setForm((prev) => ({ ...prev, totalAssets: value }))} />
              <NumberField label="월 투자 금액" value={form.monthlyInvestment} onChange={(value) => setForm((prev) => ({ ...prev, monthlyInvestment: value }))} />
            </div>
            <div className="form-grid two">
              <NumberField label="투자 기간" value={form.investmentPeriodMonths} min={1} max={600} onChange={(value) => setForm((prev) => ({ ...prev, investmentPeriodMonths: value }))} />
              <NumberField label="목표 수익률" value={form.targetReturnRate} min={1} max={1000} onChange={(value) => setForm((prev) => ({ ...prev, targetReturnRate: value }))} />
            </div>
            <div className="form-grid two">
              <SelectField label="투자 목적" value={form.investmentGoal} options={investmentGoalOptions} onChange={(value) => setForm((prev) => ({ ...prev, investmentGoal: value }))} />
              <SelectField label="투자 경험" value={form.investmentExperience} options={investmentExperienceOptions} onChange={(value) => setForm((prev) => ({ ...prev, investmentExperience: value }))} />
            </div>
            <div className="form-grid two">
              <div className="field">
                <label htmlFor="birthDate">생년월일</label>
                <input id="birthDate" type="date" value={form.birthDate} onChange={(event) => setForm((prev) => ({ ...prev, birthDate: event.target.value }))} required />
              </div>
              <SelectField label="직업" value={form.occupationType} options={occupationTypeOptions} onChange={(value) => setForm((prev) => ({ ...prev, occupationType: value }))} />
            </div>
            <div className="form-grid two">
              <SelectField label="투자 성향" value={form.investmentType} options={investmentTypeOptions} onChange={(value) => setForm((prev) => ({ ...prev, investmentType: value }))} />
              <SelectField label="변동성 허용" value={form.volatilityTolerance} options={volatilityToleranceOptions} onChange={(value) => setForm((prev) => ({ ...prev, volatilityTolerance: value }))} />
            </div>
            <div className="form-grid two">
              <SelectField label="손실 대응" value={form.lossAction} options={lossActionOptions} onChange={(value) => setForm((prev) => ({ ...prev, lossAction: value }))} />
              <SelectField label="손실 허용" value={form.lossTolerance} options={lossToleranceOptions} onChange={(value) => setForm((prev) => ({ ...prev, lossTolerance: value }))} />
            </div>
            <fieldset className="fieldset">
              <legend>레버리지</legend>
              <div className="toggle-row">
                <label className="toggle">
                  <input checked={form.leverageAllowed === true} name="leverage" type="radio" onChange={() => setForm((prev) => ({ ...prev, leverageAllowed: true }))} />
                  허용
                </label>
                <label className="toggle">
                  <input checked={form.leverageAllowed === false} name="leverage" type="radio" onChange={() => setForm((prev) => ({ ...prev, leverageAllowed: false }))} />
                  비허용
                </label>
              </div>
            </fieldset>
            {error ? <p className="error-text">{error}</p> : null}
            <div className="button-row">
              <button className="button" disabled={saving} type="submit">
                {saving ? "저장 중..." : "저장"}
              </button>
            </div>
          </form>
        )}
      </div>
    </AppShell>
  );
}

function NumberField({ label, value, onChange, min, max }: { label: string; value: number; onChange: (value: number) => void; min?: number; max?: number; }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type="number" min={min} max={max} value={value} onChange={(event) => onChange(Number(event.target.value))} required />
    </div>
  );
}

function SelectField({ label, value, options, onChange }: { label: string; value: string; options: readonly (readonly [string, string])[]; onChange: (value: string) => void; }) {
  return (
    <div className="field">
      <label>{label}</label>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </div>
  );
}
