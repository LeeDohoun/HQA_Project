export const WORKSPACE_TABS = [
  { id: "home", label: "홈" },
  { id: "watchlist", label: "워치리스트" },
  { id: "analysis", label: "AI 분석" },
  { id: "history", label: "거래 내역" },
  { id: "assets", label: "마이 페이지" }
] as const;

export type WorkspaceTab = typeof WORKSPACE_TABS[number]["id"];

export function readWorkspaceTab(value: string | null): WorkspaceTab {
  return WORKSPACE_TABS.find(tab => tab.id === value)?.id ?? "home";
}

export function workspaceTabUrl(tab: WorkspaceTab): string {
  if (tab === "assets") return "/mypage";
  return tab === "home" ? "/dashboard" : `/dashboard?tab=${tab}`;
}
