import Link from "next/link";
import type { ReactNode } from "react";

type AppShellProps = {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
};

export function AppShell({ title, subtitle, actions, children }: AppShellProps) {
  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)", display: "flex", flexDirection: "column" }}>
      {/* Topbar */}
      <header className="topbar">
        <div className="topbar-left">
          <Link className="brand-chip" href="/dashboard">HQA</Link>
          <span style={{ color: "var(--line-2)", fontSize: "0.9rem" }}>/</span>
          <span style={{ fontSize: "0.85rem", color: "var(--muted-2)", fontWeight: 500 }}>{title}</span>
        </div>
        {actions ? <div className="topbar-actions">{actions}</div> : null}
      </header>

      {/* Page content */}
      <div className="page-shell" style={{ flex: 1 }}>
        <div className="hero">
          <div>
            <h1>{title}</h1>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}
