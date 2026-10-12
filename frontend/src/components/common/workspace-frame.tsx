"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, Suspense, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { authApi, tradingApi } from "@/lib/api";
import { readWorkspaceTab, WORKSPACE_TABS, workspaceTabUrl } from "@/lib/workspace-navigation";
import type { AuthUser, Balance } from "@/types/api";
import { BoardMenu } from "./board-menu";
import styles from "./workspace-frame.module.css";

type WorkspaceState = {
  user: AuthUser | null;
  loadingUser: boolean;
  balance: Balance | null;
  balanceLoading: boolean;
  balanceError: string;
  loadBalance: () => Promise<void>;
  autoTradeEnabled: boolean;
  updateUserNickname: (nickname: string) => void;
};

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function useWorkspaceFrame() {
  return useContext(WorkspaceContext);
}

export function useWorkspace() {
  const workspace = useWorkspaceFrame();
  if (!workspace) throw new Error("WorkspaceFrame is required");
  return workspace;
}

export function WorkspaceFrame({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loadingUser, setLoadingUser] = useState(true);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balanceError, setBalanceError] = useState("");
  const [autoTradeEnabled, setAutoTradeEnabled] = useState(false);
  const [autoTradeLoading, setAutoTradeLoading] = useState(true);
  const [autoTradeSaving, setAutoTradeSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [logoutSaving, setLogoutSaving] = useState(false);
  const [message, setMessage] = useState("");
  const dialogTitle = useId();
  const dialog = useRef<HTMLElement>(null);
  const updateUserNickname = useCallback((nickname: string) => {
    setUser(current => current ? { ...current, nickname } : current);
  }, []);

  const loadBalance = useCallback(async () => {
    setBalanceLoading(true);
    setBalanceError("");
    try {
      setBalance(await tradingApi.balance());
    } catch (error) {
      setBalance(null);
      setBalanceError(error instanceof Error ? error.message : "잔고를 불러오지 못했습니다.");
    } finally {
      setBalanceLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    authApi.me().then(response => { if (active) setUser(response); })
      .catch(() => { if (active) setUser(null); })
      .finally(() => { if (active) setLoadingUser(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!user) return;
    let active = true;
    if (user.kisConfigured) void loadBalance();
    tradingApi.status().then(status => { if (active) setAutoTradeEnabled(status.enabled); })
      .catch(() => { if (active) setAutoTradeEnabled(false); })
      .finally(() => { if (active) setAutoTradeLoading(false); });
    return () => { active = false; };
  }, [user?.id, user?.kisConfigured, loadBalance]);

  useEffect(() => {
    if (!confirmOpen) return;
    const previous = document.activeElement;
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, [confirmOpen]);

  async function confirmAutoTradeToggle() {
    if (autoTradeSaving) return;
    setAutoTradeSaving(true);
    setMessage("");
    try {
      const status = await tradingApi.setAuto(!autoTradeEnabled);
      setAutoTradeEnabled(status.enabled);
      setConfirmOpen(false);
      setMessage(status.enabled ? "자동매매를 켰습니다." : "자동매매를 껐습니다.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "자동매매 설정에 실패했습니다.");
    } finally {
      setAutoTradeSaving(false);
    }
  }

  async function logout() {
    if (logoutSaving) return;
    setLogoutSaving(true);
    try {
      await authApi.logout();
      setUser(null);
      setConfirmOpen(false);
      setAutoTradeEnabled(false);
      setBalance(null);
      router.push("/login");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "로그아웃에 실패했습니다.");
      setLogoutSaving(false);
    }
  }

  const workspace = useMemo(() => ({ user, loadingUser, balance, balanceLoading, balanceError, loadBalance, autoTradeEnabled, updateUserNickname }),
    [user, loadingUser, balance, balanceLoading, balanceError, loadBalance, autoTradeEnabled, updateUserNickname]);

  const accountActions = user ? (
    <>
      {user.kisConfigured ? (
        <Link className={styles.balance} href="/mypage" title="KIS 계좌 전체 잔고">
          <small>계정 전체 잔고</small>
          <b>{balanceLoading ? "불러오는 중..." : balance?.summary?.totalEvalAmount != null
            ? `${new Intl.NumberFormat("ko-KR").format(Math.round(balance.summary.totalEvalAmount))}원` : "-"}</b>
        </Link>
      ) : null}
      <button className={styles.status} type="button" data-enabled={autoTradeEnabled}
        disabled={autoTradeLoading || autoTradeSaving} onClick={() => { setMessage(""); setConfirmOpen(true); }}>
        <span className={styles.dot} data-live={autoTradeEnabled} />
        {autoTradeLoading ? "자동매매 확인 중..." : autoTradeSaving ? "변경 중..." : `자동매매 ${autoTradeEnabled ? "ON" : "OFF"}`}
      </button>
      <button className={styles.logout} type="button" disabled={logoutSaving} onClick={logout}>
        {logoutSaving ? "로그아웃 중..." : "로그아웃"}
      </button>
    </>
  ) : loadingUser ? <span className={styles.link}>불러오는 중...</span> : <Link className={styles.link} href="/login">로그인</Link>;

  return (
    <WorkspaceContext.Provider value={workspace}>
      <div className={`hqa-app-theme ${styles.frame}`}>
        <Suspense fallback={<WorkspaceHeader role={user?.role ?? null} accountActions={accountActions} />}>
          <ActiveWorkspaceHeader role={user?.role ?? null} accountActions={accountActions} />
        </Suspense>
        {message && !confirmOpen ? <p className={styles.notice} role="status">{message}</p> : null}
        {children}
        {confirmOpen ? (
          <div className={styles.backdrop} role="presentation" onMouseDown={() => { if (!autoTradeSaving) setConfirmOpen(false); }}>
            <section ref={dialog} className={styles.modal} role="dialog" aria-modal="true" aria-labelledby={dialogTitle}
              onMouseDown={event => event.stopPropagation()} onKeyDown={event => {
                if (event.key === "Escape" && !autoTradeSaving) { event.preventDefault(); setConfirmOpen(false); }
                if (event.key !== "Tab") return;
                const buttons = Array.from(dialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
                if (!buttons.length) { event.preventDefault(); return; }
                const target = event.shiftKey ? buttons[buttons.length - 1] : buttons[0];
                if (document.activeElement === (event.shiftKey ? buttons[0] : buttons[buttons.length - 1])) {
                  event.preventDefault(); target.focus();
                }
              }}>
              <div className={styles.kicker}><span className={styles.dot} data-live={autoTradeEnabled} />AUTO TRADING</div>
              <h2 id={dialogTitle} className={styles.title}>자동매매를 {autoTradeEnabled ? "중지할까요?" : "시작할까요?"}</h2>
              <p className={styles.copy}>{autoTradeEnabled
                ? "OFF로 전환하면 AI 자동매매 루프를 중지하고, 이후 대기 신호도 집행하지 않습니다."
                : "ON으로 전환하면 모의투자 자동매매 루프가 시작되고, 백엔드 스케줄러도 이 계정을 자동매매 대상으로 처리합니다."}</p>
              <ul className={styles.list}>
                <li>모의투자 KIS 계정 기준으로 주문 흐름을 실행합니다.</li>
                <li>생성된 매매 판단과 거절 사유는 거래 내역의 AI 매매근거에서 확인할 수 있습니다.</li>
              </ul>
              {message ? <p className={styles.copy} role="alert">{message}</p> : null}
              <div className={styles.actions}>
                <button className={styles.button} type="button" disabled={autoTradeSaving} onClick={() => setConfirmOpen(false)}>취소</button>
                <button className={styles.button} type="button" data-confirm="true" disabled={autoTradeSaving} onClick={confirmAutoTradeToggle}>
                  {autoTradeSaving ? "처리 중..." : autoTradeEnabled ? "자동매매 끄기" : "자동매매 켜기"}
                </button>
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </WorkspaceContext.Provider>
  );
}

type HeaderProps = { role: AuthUser["role"] | null; accountActions: ReactNode; activeTab?: string };

function ActiveWorkspaceHeader(props: HeaderProps) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  return <WorkspaceHeader {...props} activeTab={pathname === "/mypage" ? "assets" : pathname === "/dashboard" ? readWorkspaceTab(searchParams.get("tab")) : undefined} />;
}

function WorkspaceHeader({ role, accountActions, activeTab }: HeaderProps) {
  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link className={styles.mark} href="/dashboard" aria-label="HQA 홈"><b>HQA</b><i aria-hidden="true" /></Link>
        <nav className={styles.links} aria-label="주 메뉴">
          {WORKSPACE_TABS.map(tab => <Link key={tab.id} className={styles.link} href={workspaceTabUrl(tab.id)}
            aria-current={activeTab === tab.id ? "page" : undefined}>{tab.label}</Link>)}
          <BoardMenu role={role} />
        </nav>
        <div className={styles.right}>{accountActions}</div>
      </div>
    </header>
  );
}
