"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { BoardMenu } from "./board-menu";
import { useWorkspaceFrame } from "./workspace-frame";

type AppShellProps = {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  wide?: boolean;
  children: ReactNode;
};

export function AppShell({ title, subtitle, actions, wide = false, children }: AppShellProps) {
  const workspace = useWorkspaceFrame();
  return (
    <div style={{ minHeight: workspace ? undefined : "100vh", background: "var(--bg)", display: "flex", flexDirection: "column" }}>
      {!workspace ? <header className="topbar">
        <div className="topbar-left">
          <Link className="brand-chip" href="/dashboard">
            <span aria-hidden style={{ fontSize: "0.95rem" }}>◒</span>
            HQA
          </Link>
          <span style={{ color: "var(--line-2)", fontSize: "0.9rem" }}>/</span>
          <span style={{ fontSize: "0.9rem", color: "var(--muted-2)", fontWeight: 600 }}>{title}</span>
        </div>
        <div className="topbar-actions" style={{ flexWrap: "wrap" }}>
          <nav aria-label="주 메뉴" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0.25rem" }}>
            <Link className="button-ghost" href="/dashboard">대시보드</Link>
            <BoardMenu />
          </nav>
          {actions}
        </div>
      </header> : null}

      <div className={wide ? "page-shell page-shell-wide anim-fade-up" : "page-shell anim-fade-up"} style={{ flex: 1 }}>
        <div className="hero" style={workspace ? { display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "1rem" } : undefined}>
          <div>
            <h1>{title}</h1>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          {workspace && actions ? <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>{actions}</div> : null}
        </div>
        {children}
      </div>
    </div>
  );
}
