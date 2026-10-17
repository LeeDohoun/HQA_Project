export const WORKSPACE_TABS = [
  { id: "home", label: "홈" },
  { id: "watchlist", label: "워치리스트" },
  { id: "analysis", label: "AI 분석" },
  { id: "history", label: "거래 내역" },
  { id: "strategies", label: "전략" },
  { id: "assets", label: "마이 페이지" }
] as const;

export type WorkspaceTab = typeof WORKSPACE_TABS[number]["id"];

// Tabs that are separate pages rather than sections of /dashboard.
const PAGE_TAB_URLS: Partial<Record<WorkspaceTab, string>> = { assets: "/mypage", strategies: "/strategies" };

export function readWorkspaceTab(value: string | null): WorkspaceTab {
  return WORKSPACE_TABS.find(tab => tab.id === value)?.id ?? "home";
}

export function workspaceTabUrl(tab: WorkspaceTab): string {
  const page = PAGE_TAB_URLS[tab];
  if (page) return page;
  return tab === "home" ? "/dashboard" : `/dashboard?tab=${tab}`;
}

/** Active header tab for a workspace pathname; `tabParam` is the dashboard's `tab` query value. */
export function activeWorkspaceTab(pathname: string, tabParam: string | null): WorkspaceTab | undefined {
  const page = WORKSPACE_TABS.find(tab => PAGE_TAB_URLS[tab.id] === pathname);
  if (page) return page.id;
  return pathname === "/dashboard" ? readWorkspaceTab(tabParam) : undefined;
}
