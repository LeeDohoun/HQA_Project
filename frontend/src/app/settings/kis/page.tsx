"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";
import type { KisCredentials, KisCredentialsStatus } from "@/types/api";

const emptyKis: KisCredentials = {
  kisAppKey: "",
  kisAppSecret: "",
  kisAccountNo: "",
  kisAccountProductCode: "01"
};

export default function KisSettingsPage() {
  const router = useRouter();
  const [form, setForm] = useState<KisCredentials>(emptyKis);
  const [status, setStatus] = useState<KisCredentialsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedMessage, setSavedMessage] = useState("");

  useEffect(() => {
    let active = true;
    authApi
      .getKis()
      .then((current) => {
        if (!active) return;
        setStatus(current);
        if (current.kisAccountProductCode) {
          setForm((prev) => ({ ...prev, kisAccountProductCode: current.kisAccountProductCode! }));
        }
      })
      .catch(() => {
        // not configured yet — that's fine
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const valid =
    form.kisAppKey.trim().length > 0 &&
    form.kisAppSecret.trim().length > 0 &&
    form.kisAccountNo.trim().length > 0 &&
    form.kisAccountProductCode.trim().length > 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setSavedMessage("");
    try {
      const updated = await authApi.saveKis(form);
      setStatus(updated);
      setForm((prev) => ({ ...prev, kisAppKey: "", kisAppSecret: "", kisAccountNo: "" }));
      setSavedMessage("저장되었어요.");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "저장에 실패했습니다.");
    } finally {
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
          onClick={() => router.back()}
        >
          ←
        </button>
        <span className="wiz-step-count">KIS 설정</span>
      </div>

      <section className="wiz-card">
        <span className="wiz-emoji" aria-hidden>🔑</span>
        <h1 className="wiz-question">KIS API 키 관리</h1>
        <p className="wiz-hint">
          한국투자증권 OpenAPI 키예요. 저장 시 암호화되어 DB에 보관돼요.
        </p>

        {status?.configured ? (
          <div className="wiz-summary" style={{ marginBottom: 12 }}>
            <div className="wiz-summary-row">
              <span>현재 App Key</span>
              <span>{status.kisAppKeyMasked}</span>
            </div>
            <div className="wiz-summary-row">
              <span>현재 계좌번호</span>
              <span>{status.kisAccountNoMasked}</span>
            </div>
            <div className="wiz-summary-row">
              <span>계좌상품코드</span>
              <span>{status.kisAccountProductCode}</span>
            </div>
          </div>
        ) : (
          <p className="wiz-hint">아직 등록된 키가 없어요.</p>
        )}

        <form onSubmit={submit}>
          <div className="field">
            <label>App Key</label>
            <input
              className="wiz-input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={form.kisAppKey}
              placeholder={status?.configured ? "새 값으로 덮어쓸 때만 입력" : "PSxxxxxxxxxxxxxxxxxx"}
              onChange={(e) => setForm({ ...form, kisAppKey: e.target.value })}
            />
          </div>

          <div className="field">
            <label>App Secret</label>
            <input
              className="wiz-input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={form.kisAppSecret}
              placeholder={status?.configured ? "새 값으로 덮어쓸 때만 입력" : "발급받은 시크릿 키"}
              onChange={(e) => setForm({ ...form, kisAppSecret: e.target.value })}
            />
          </div>

          <div className="field">
            <label>계좌번호 (CANO)</label>
            <input
              className="wiz-input"
              type="password"
              inputMode="numeric"
              autoComplete="off"
              value={form.kisAccountNo}
              placeholder={status?.configured ? "새 값으로 덮어쓸 때만 입력" : "12345678"}
              onChange={(e) => setForm({ ...form, kisAccountNo: e.target.value })}
            />
          </div>

          <div className="field">
            <label>계좌상품코드 (ACNT_PRDT_CD)</label>
            <input
              className="wiz-input"
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={form.kisAccountProductCode}
              placeholder="01"
              onChange={(e) => setForm({ ...form, kisAccountProductCode: e.target.value })}
            />
          </div>

          {error ? <p className="error-text">{error}</p> : null}
          {savedMessage ? <p className="meta">{savedMessage}</p> : null}

          <button type="submit" className="wiz-cta" disabled={!valid || saving}>
            {saving ? "저장 중..." : "저장하기"}
          </button>
        </form>
      </section>
    </div>
  );
}
