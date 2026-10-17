import type { BoardType } from "@/types/api";

type SearchParams = Pick<URLSearchParams, "get">;

export type CommunityListState = {
  board: BoardType;
  stockCode: string;
  page: number;
};

function boardType(value: string | null): BoardType {
  return value === "STOCK" || value === "INQUIRY" ? value : "FREE";
}

function pageNumber(value: string | null): number {
  if (value === null || !/^(0|[1-9]\d*)$/.test(value)) return 0;
  const page = Number(value);
  return Number.isInteger(page) && page <= 2147483647 ? page : 0;
}

export function readCommunityListState(params: SearchParams): CommunityListState {
  const board = boardType(params.get("board"));
  return {
    board,
    stockCode: board === "STOCK" ? (params.get("stockCode") ?? "").trim() : "",
    page: pageNumber(params.get("page"))
  };
}

export function communityListUrl(state: CommunityListState): string {
  const params = new URLSearchParams({ board: state.board });
  if (state.board === "STOCK" && state.stockCode.trim()) params.set("stockCode", state.stockCode.trim());
  if (state.page > 0) params.set("page", String(state.page));
  return `/community?${params.toString()}`;
}

/** Return destinations are canonical local list URLs, never arbitrary redirects. */
export function communityReturnUrl(params: SearchParams, fallbackBoard: BoardType = "FREE"): string {
  const value = params.get("returnTo");
  if (value) {
    try {
      const url = new URL(value, "https://community.invalid");
      if (url.origin === "https://community.invalid" && url.pathname === "/community") {
        return communityListUrl(readCommunityListState(url.searchParams));
      }
    } catch {
      // Invalid destinations fall back to the current board's first page.
    }
  }
  return communityListUrl({ board: fallbackBoard, stockCode: "", page: 0 });
}

export function communityPostUrl(postId: string, returnTo: string): string {
  return `/community/${encodeURIComponent(postId)}?${new URLSearchParams({ returnTo })}`;
}

export function communityEditUrl(postId: string, returnTo: string): string {
  return `/community/${encodeURIComponent(postId)}/edit?${new URLSearchParams({ returnTo })}`;
}

export function communityNewUrl(options: {
  board: BoardType;
  returnTo: string;
  stockCode?: string;
  targetPostId?: string;
}): string {
  const params = new URLSearchParams({ board: options.board, returnTo: options.returnTo });
  if (options.board === "STOCK" && options.stockCode) params.set("stockCode", options.stockCode);
  if (options.board === "INQUIRY" && options.targetPostId) params.set("targetPostId", options.targetPostId);
  return `/community/new?${params.toString()}`;
}