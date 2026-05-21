import Link from "next/link";
import type { ReactNode } from "react";

type AppShellProps = {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  wide?: boolean;
  children: ReactNode;
};

export function AppShell({ title, subtitle, actions, wide = false, children }: AppShellProps) {
  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)", display: "flex", flexDirection: "column" }}>
      <header className="topbar">
        <div className="topbar-left">
          <Link className="brand-chip" href="/dashboard">
            <span aria-hidden style={{ fontSize: "0.95rem" }}>H</span>
            HQA
          </Link>
          <span style={{ color: "var(--line-2)", fontSize: "0.9rem" }}>/</span>
          <span style={{ fontSize: "0.9rem", color: "var(--muted-2)", fontWeight: 600 }}>{title}</span>
        </div>
        {actions ? <div className="topbar-actions">{actions}</div> : null}
      </header>

      <div className={wide ? "page-shell page-shell-wide anim-fade-up" : "page-shell anim-fade-up"} style={{ flex: 1 }}>
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
