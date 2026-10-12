"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AppShell } from "@/components/common/app-shell";
import { communityApi } from "@/lib/api";
import { communityPostUrl, communityReturnUrl } from "@/lib/community-navigation";
import type { BoardType } from "@/types/api";

const BOARD_LABEL: Record<BoardType, string> = {
  FREE: "자유게시판",
  STOCK: "종목토론방",
  INQUIRY: "문의"
};

function isBoardType(value: string | null): value is BoardType {
  return value === "FREE" || value === "STOCK" || value === "INQUIRY";
}

function NewPostInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const boardParam = searchParams.get("board");
  const board: BoardType = isBoardType(boardParam) ? boardParam : "FREE";
  const returnUrl = communityReturnUrl(searchParams, board);
  /** 삭제 요청으로 넘어온 경우. 문의 본문에 대상 글을 묶어 보낸다. */
  const targetPostId = searchParams.get("targetPostId") ?? "";

  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [stockCode, setStockCode] = useState(searchParams.get("stockCode") ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      let created;
      if (board === "FREE") {
        created = await communityApi.createFree({ title, content });
      } else if (board === "STOCK") {
        created = await communityApi.createStock({ title, content, stockCode: stockCode.trim() });
      } else {
        created = await communityApi.createInquiry({
          title,
          content,
          targetPostId: targetPostId || undefined
        });
      }
      router.push(communityPostUrl(created.id, returnUrl));
    } catch (err) {
      setError(err instanceof Error ? err.message : "저장하지 못했습니다.");
      setSubmitting(false);
    }
  }

  return (
    <AppShell
      title={`${BOARD_LABEL[board]} 글쓰기`}
      subtitle={
        board === "INQUIRY" && targetPostId
          ? "삭제를 요청할 글이 함께 전달됩니다."
          : board === "INQUIRY"
            ? "관리자만 열람합니다."
            : undefined
      }
      actions={
        <Link className="button-ghost" href={returnUrl}>
          목록
        </Link>
      }
    >
      <form className="card" onSubmit={submit} style={{ display: "grid", gap: "1rem" }}>
        {board !== "INQUIRY" ? <p style={{ color: "var(--moss)", margin: 0, fontSize: "0.85rem" }}>글을 작성하면 이 게시판 포인트 10P를 받습니다. 삭제한 활동의 포인트는 회수됩니다.</p> : null}
        {board === "INQUIRY" && targetPostId ? (
          <div style={{ color: "var(--muted-2)", fontSize: "0.9rem" }}>
            대상 글: <Link href={communityPostUrl(targetPostId, returnUrl)}>{targetPostId}</Link>
          </div>
        ) : null}

        {board === "STOCK" ? (
          <label style={{ display: "grid", gap: "0.35rem" }}>
            <span>종목코드</span>
            <input
              value={stockCode}
              onChange={(event) => setStockCode(event.target.value)}
              placeholder="005930"
              maxLength={12}
              required
            />
          </label>
        ) : null}

        <label style={{ display: "grid", gap: "0.35rem" }}>
          <span>제목</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={160}
            required
          />
        </label>

        <label style={{ display: "grid", gap: "0.35rem" }}>
          <span>내용</span>
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            rows={14}
            maxLength={20000}
            required
          />
        </label>

        {error ? <div style={{ color: "var(--bad)" }}>{error}</div> : null}

        <div className="button-row">
          <button type="submit" className="button" disabled={submitting}>
            {submitting ? "저장 중…" : "등록"}
          </button>
          <Link className="button-secondary" href={returnUrl}>
            취소
          </Link>
        </div>
      </form>
    </AppShell>
  );
}

export default function NewPostPage() {
  return (
    <Suspense fallback={<AppShell title="글쓰기">불러오는 중…</AppShell>}>
      <NewPostInner />
    </Suspense>
  );
}
