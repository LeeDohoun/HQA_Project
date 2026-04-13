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
    <div className="page-shell">
      <header className="hero">
        <div>
          <Link className="hero-badge" href="/dashboard">
            HQA 작업공간
          </Link>
          <h1>{title}</h1>
        </div>
        {actions ? <div className="hero-actions">{actions}</div> : null}
      </header>
      {children}
    </div>
  );
}
