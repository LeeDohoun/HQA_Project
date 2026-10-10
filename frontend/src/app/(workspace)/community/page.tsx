"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { AppShell } from "@/components/common/app-shell";
import { AuthorBadge } from "@/components/community/author-badge";
import { communityApi } from "@/lib/api";
import { communityListUrl, communityNewUrl, communityPostUrl, readCommunityListState } from "@/lib/community-navigation";
import type { BoardType, PostListResult } from "@/types/api";

const BOARDS: { value: BoardType; label: string; hint: string }[] = [
  { value: "FREE", label: "자유게시판", hint: "자유롭게 이야기하는 공간입니다." },
  { value: "STOCK", label: "종목토론방", hint: "종목별로 의견을 나눕니다." },
  { value: "INQUIRY", label: "문의", hint: "관리자에게 보내는 문의입니다. 본인과 관리자만 볼 수 있습니다." }
];
const PAGE_SIZE = 20;

type ListRequest = {
  key: string;
  status: "loading" | "ready" | "error";
  result: PostListResult | null;
  error: string | null;
  errorStatus?: number;
};

function formatDate(iso: string) {
  const d = new Date(iso);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function CommunityPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const state = readCommunityListState(searchParams);
  const { board, stockCode: stockFilter, page } = state;
  const listUrl = communityListUrl(state);
  const [request, setRequest] = useState<ListRequest>({ key: "", status: "loading", result: null, error: null });
  const [retry, setRetry] = useState(0);
  const [stockInput, setStockInput] = useState(stockFilter);

  useEffect(() => {
    setStockInput(stockFilter);
  }, [board, stockFilter]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setRequest({ key: listUrl, status: "loading", result: null, error: null });
    void (async () => {
      try {
        const data = board === "FREE"
          ? await communityApi.listFree(page, PAGE_SIZE, controller.signal)
          : board === "STOCK"
            ? await communityApi.listStock(stockFilter || undefined, page, PAGE_SIZE, controller.signal)
            : await communityApi.listInquiries(page, PAGE_SIZE, controller.signal);
        if (!active) return;
        const lastPage = Math.max(0, data.totalPages - 1);
        if (page > lastPage) {
          router.replace(communityListUrl({ board, stockCode: stockFilter, page: lastPage }), { scroll: false });
          return;
        }
        setRequest({ key: listUrl, status: "ready", result: data, error: null });
      } catch (err) {
        if (!active || controller.signal.aborted) return;
        setRequest({ key: listUrl, status: "error", result: null,
          error: err instanceof Error ? err.message : "목록을 불러오지 못했습니다.",
          errorStatus: err instanceof Error && "status" in err && typeof err.status === "number" ? err.status : undefined });
      }
    })();
    return () => {
      active = false;
      controller.abort();
    };
  }, [board, page, stockFilter, listUrl, router, retry]);

  // During navigation, never render rows fetched for a different list URL.
  const current = request.key === listUrl;
  const loading = !current || request.status === "loading";
  const result = current ? request.result : null;
  const error = current ? request.error : null;
  const loginRequired = current && request.errorStatus === 401;

  function applyStockFilter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    router.push(communityListUrl({ board: "STOCK", stockCode: stockInput.trim(), page: 0 }), { scroll: false });
  }

  function selectPage(next: number) {
    router.push(communityListUrl({ board, stockCode: stockFilter, page: next }), { scroll: false });
  }

  const activeBoard = BOARDS.find((item) => item.value === board)!;
  return (
    <AppShell title={activeBoard.label} subtitle={activeBoard.hint} actions={
      <Link className="button" href={communityNewUrl({ board, returnTo: listUrl, stockCode: stockFilter })}>글쓰기</Link>
    }>
      {board === "STOCK" ? (
        <form onSubmit={applyStockFilter} style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
          <input aria-label="종목코드 필터" value={stockInput} onChange={(event) => setStockInput(event.target.value)}
            placeholder="종목코드로 거르기 (예: 005930)" style={{ flex: 1, minWidth: 0, maxWidth: "18rem" }} />
          <button type="submit" className="button-secondary">필터</button>
          {stockFilter ? <Link className="button-ghost" href={communityListUrl({ board: "STOCK", stockCode: "", page: 0 })}>해제</Link> : null}
        </form>
      ) : null}
      {loading ? <div className="card" role="status">불러오는 중…</div> : error ? (
        <div className="card" role="alert">
          <div style={{ color: "var(--bad)" }}>{loginRequired ? "게시판을 이용하려면 로그인해 주세요." : error}</div>
          {loginRequired ? (
            <Link className="button" style={{ marginTop: "0.75rem" }} href="/login">로그인</Link>
          ) : (
            <button type="button" className="button-secondary" style={{ marginTop: "0.75rem" }} onClick={() => setRetry((value) => value + 1)}>다시 시도</button>
          )}
        </div>
      ) : result && result.items.length > 0 ? (
        <>
          <div className="card" style={{ padding: 0, overflowX: "auto" }}>
            <table className="backtest-table">
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>제목</th>
                  {board === "STOCK" ? <th style={{ width: "6rem" }}>종목</th> : null}
                  {board === "INQUIRY" ? <th style={{ width: "6rem" }}>상태</th> : null}
                  <th style={{ width: "10rem" }}>작성자</th>
                  {board !== "INQUIRY" ? <th style={{ width: "4rem" }}>추천</th> : null}
                  <th style={{ width: "4rem" }}>댓글</th>
                  <th style={{ width: "10rem" }}>작성일</th>
                </tr>
              </thead>
              <tbody>
                {result.items.map((post) => (
                  <tr key={post.id}>
                    <td style={{ textAlign: "left" }}><Link href={communityPostUrl(post.id, listUrl)}>{post.title}</Link></td>
                    {board === "STOCK" ? <td>{post.stockCode ?? "-"}</td> : null}
                    {board === "INQUIRY" ? <td><span className="community-status" data-status={post.inquiryStatus}>{post.inquiryStatus ?? "-"}</span></td> : null}
                    <td><AuthorBadge author={post.author} board={board} /></td>
                    {board !== "INQUIRY" ? <td>{post.recommendationCount}</td> : null}
                    <td>{post.commentCount}</td><td>{formatDate(post.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="button-row" style={{ marginTop: "1rem", justifyContent: "center" }}>
            <button type="button" className="button-secondary" disabled={page <= 0} onClick={() => selectPage(page - 1)}>이전</button>
            <span style={{ alignSelf: "center", color: "var(--muted-2)" }}>
              {result.page + 1} / {result.totalPages} · 전체 {result.totalItems}건
            </span>
            <button type="button" className="button-secondary" disabled={result.page + 1 >= result.totalPages} onClick={() => selectPage(page + 1)}>다음</button>
          </div>
        </>
      ) : (
        <div className="empty-state">{board === "INQUIRY" ? "보낸 문의가 없습니다." : "아직 글이 없습니다. 첫 글을 남겨보세요."}</div>
      )}
    </AppShell>
  );
}

export default function CommunityPage() {
  return <Suspense fallback={<AppShell title="게시판">불러오는 중…</AppShell>}><CommunityPageInner /></Suspense>;
}
