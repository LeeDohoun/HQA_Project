"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/common/app-shell";
import { AuthorBadge } from "@/components/community/author-badge";
import { communityApi } from "@/lib/api";
import { mergeComments, refreshPostComments } from "@/lib/community-comments";
import { communityEditUrl, communityNewUrl, communityPostUrl, communityReturnUrl } from "@/lib/community-navigation";
import type { PostComment, PostDetail } from "@/types/api";
import { InquiryResolveForm } from "@/components/community/inquiry-resolve-form";
import type { InquiryResolution } from "@/components/community/inquiry-resolve-form";

const INQUIRY_STATUS_LABEL = { OPEN: "접수", RESOLVED: "처리 완료", REJECTED: "요청 거절" };

function formatDate(iso: string) {
  const d = new Date(iso);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export default function PostDetailPage() {
  const router = useRouter();
  const params = useParams<{ postId: string }>();
  const postId = params.postId;
  const searchParams = useSearchParams();

  const [post, setPost] = useState<PostDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const requestId = useRef(0);
  const viewVersion = useRef(0);
  const detailRequest = useRef<AbortController | null>(null);
  const moreRequest = useRef<AbortController | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const actionsBusy = busy || loadingMore;
  const returnUrl = communityReturnUrl(searchParams, post?.boardType ?? "FREE");

  const load = useCallback(async (previous?: PostDetail, created?: PostComment, response?: PostDetail) => {
    const refresh = !!previous;
    const currentRequest = ++requestId.current;
    detailRequest.current?.abort();
    moreRequest.current?.abort();
    moreRequest.current = null;
    setLoadingMore(false);
    setMoreError(null);
    const controller = new AbortController();
    detailRequest.current = controller;
    if (!refresh) setLoading(true);
    setError(null);
    try {
      const data = response ?? await communityApi.get(postId, controller.signal);
      if (requestId.current !== currentRequest || controller.signal.aborted) return;
      const updated = refresh ? await refreshPostComments(previous, data,
        (cursor) => communityApi.listComments(postId, cursor, 20, controller.signal),
        (ids) => communityApi.visibleComments(postId, ids, controller.signal), created) : data;
      if (requestId.current === currentRequest && !controller.signal.aborted) setPost(updated);
    } catch (err) {
      if (requestId.current !== currentRequest || controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : "글을 불러오지 못했습니다.";
      if (refresh) {
        setActionError(`변경은 저장했지만 최신 글 정보를 불러오지 못했습니다. ${message}`);
      } else {
        setError(message);
        setPost(null);
      }
    } finally {
      if (requestId.current === currentRequest) setLoading(false);
    }
  }, [postId]);

  useEffect(() => {
    viewVersion.current++;
    setBusy(false);
    setActionError(null);
    setActionMessage(null);
    setComment("");
    void load();
    return () => {
      viewVersion.current++;
      requestId.current++;
      detailRequest.current?.abort();
      moreRequest.current?.abort();
    };
  }, [load]);

  async function submitComment(event: React.FormEvent) {
    event.preventDefault();
    if (actionsBusy || !post || !comment.trim()) return;
    const view = viewVersion.current;
    setBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      const created = await communityApi.addComment(postId, comment.trim());
      if (viewVersion.current !== view) return;
      setPost((current) => current?.id === postId ? {
        ...current, comments: mergeComments(current.comments, [created]), commentCount: current.commentCount + 1
      } : current);
      setComment("");
      setActionMessage("댓글을 등록했습니다.");
      await load(post, created);
    } catch (err) {
      if (viewVersion.current !== view) return;
      setActionError(err instanceof Error ? err.message : "댓글을 달지 못했습니다.");
    } finally {
      if (viewVersion.current === view) setBusy(false);
    }
  }

  async function removeComment(commentId: string) {
    if (actionsBusy || !post || !window.confirm("이 댓글을 삭제할까요?")) return;
    const view = viewVersion.current;
    setBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      await communityApi.removeComment(commentId);
      if (viewVersion.current !== view) return;
      setPost((current) => current?.id === postId ? {
        ...current, comments: current.comments.filter((item) => item.id !== commentId),
        commentCount: Math.max(0, current.commentCount - 1)
      } : current);
      setActionMessage("댓글을 삭제했습니다.");
      await load(post);
    } catch (err) {
      if (viewVersion.current !== view) return;
      setActionError(err instanceof Error ? err.message : "댓글을 지우지 못했습니다.");
    } finally {
      if (viewVersion.current === view) setBusy(false);
    }
  }

  async function recommend() {
    if (actionsBusy || !post?.recommendable) return;
    const view = viewVersion.current;
    setBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      const updated = await communityApi.recommend(postId);
      if (viewVersion.current !== view) return;
      setActionMessage("추천했습니다. 작성자에게 5P가 지급됩니다. 추천은 취소할 수 없습니다.");
      await load(post, undefined, updated);
    } catch (err) {
      if (viewVersion.current !== view) return;
      setActionError(err instanceof Error ? err.message : "추천하지 못했습니다.");
      if (err instanceof Error && "status" in err && err.status === 409) await load(post);
    } finally {
      if (viewVersion.current === view) setBusy(false);
    }
  }

  async function removePost() {
    if (actionsBusy || !post || !window.confirm("이 글을 삭제할까요?")) return;
    const view = viewVersion.current;
    setBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      await communityApi.remove(postId);
      if (viewVersion.current !== view) return;
      router.push(returnUrl);
    } catch (err) {
      if (viewVersion.current !== view) return;
      setActionError(err instanceof Error ? err.message : "삭제하지 못했습니다.");
      if (viewVersion.current === view) setBusy(false);
    }
  }

  async function resolveInquiry(payload: InquiryResolution): Promise<boolean> {
    if (actionsBusy || !post?.inquiryResolvable) return false;
    const view = viewVersion.current;
    setBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      const updated = await communityApi.resolveInquiry(postId, payload);
      if (viewVersion.current !== view) return false;
      setPost((current) => current?.id === postId ? { ...updated, comments: current.comments,
        nextCommentCursor: current.nextCommentCursor, hasMoreComments: current.hasMoreComments } : updated);
      await load(post, undefined, updated);
      if (viewVersion.current !== view) return false;
      setActionMessage("문의 처리 결과를 저장했습니다.");
      return true;
    } catch (err) {
      if (viewVersion.current !== view) return false;
      setActionError(err instanceof Error ? err.message : "문의 처리 결과를 저장하지 못했습니다.");
      return false;
    } finally {
      if (viewVersion.current === view) setBusy(false);
    }
  }

  async function loadMoreComments() {
    if (!post?.hasMoreComments || !post.nextCommentCursor || actionsBusy || moreRequest.current) return;
    const cursor = post.nextCommentCursor;
    const currentRequest = requestId.current;
    const controller = new AbortController();
    moreRequest.current = controller;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await communityApi.listComments(postId, cursor, 20, controller.signal);
      if (requestId.current !== currentRequest || controller.signal.aborted) return;
      setPost((current) => current?.id === postId && current.nextCommentCursor === cursor ? {
        ...current, comments: mergeComments(current.comments, page.items), commentCount: page.totalItems,
        nextCommentCursor: page.nextCursor, hasMoreComments: page.hasMore
      } : current);
    } catch (err) {
      if (requestId.current === currentRequest && !controller.signal.aborted) {
        setMoreError(err instanceof Error ? err.message : "댓글을 더 불러오지 못했습니다.");
      }
    } finally {
      if (moreRequest.current === controller) {
        moreRequest.current = null;
        if (requestId.current === currentRequest) setLoadingMore(false);
      }
    }
  }

  if (loading || (post !== null && post.id !== postId)) {
    return <AppShell title="글">불러오는 중…</AppShell>;
  }

  if (error || !post) {
    return (
      <AppShell title="글" actions={<Link className="button-ghost" href={returnUrl}>목록</Link>}>
        <div className="card" style={{ color: "var(--bad)" }}>{error ?? "글을 찾을 수 없습니다."}</div>
      </AppShell>
    );
  }

  const isInquiry = post.boardType === "INQUIRY";

  return (
    <AppShell
      title={post.title}
      subtitle={<span style={{ display: "inline-flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
        <AuthorBadge author={post.author} board={post.boardType} />
        <span>{formatDate(post.createdAt)}{post.stockCode ? ` · ${post.stockCode}` : ""}</span>
      </span>}
      actions={
        <Link className="button-ghost" href={returnUrl}>
          목록
        </Link>
      }
    >
      <article className="card" style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>
        {post.content}
      </article>

      {!isInquiry ? (
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0.75rem", marginTop: "1rem" }}>
          <button type="button" className={post.recommended ? "button" : "button-secondary"}
            aria-pressed={post.recommended} disabled={actionsBusy || !post.recommendable} onClick={recommend}>
            {post.recommended ? "추천 완료" : "추천"} · {post.recommendationCount}
          </button>
          <span style={{ color: "var(--ink-2)", fontSize: "0.8rem" }}>
            {post.editable ? "자기 글은 추천할 수 없습니다." : "1인당 한 번 · 추천 취소 불가 · 작성자에게 5P 지급"}
          </span>
        </div>
      ) : null}

      {isInquiry ? (
        <div className="card" style={{ marginTop: "1rem" }}>
          <div style={{ color: "var(--muted-2)", fontSize: "0.9rem" }}>처리 상태</div>
          <div style={{ fontWeight: 600 }}>{post.inquiryStatus ? INQUIRY_STATUS_LABEL[post.inquiryStatus] : "-"}</div>
          {post.targetPostId ? (
            <div style={{ marginTop: "0.5rem" }}>
              대상 글: <Link href={communityPostUrl(post.targetPostId, returnUrl)}>{post.targetPostId}</Link>
            </div>
          ) : null}
          {post.adminReply ? (
            <div style={{ marginTop: "0.75rem", whiteSpace: "pre-wrap" }}>
              <div style={{ color: "var(--muted-2)", fontSize: "0.9rem" }}>관리자 답변</div>
              {post.adminReply}
            </div>
          ) : null}
        </div>
      ) : null}

      {post.inquiryResolvable ? <InquiryResolveForm key={post.id} post={post} busy={actionsBusy} onResolve={resolveInquiry} /> : null}

      <div className="button-row" style={{ marginTop: "1rem" }}>
        {post.editable && !actionsBusy ? (
          <Link className="button-secondary" href={communityEditUrl(post.id, returnUrl)}>수정</Link>
        ) : null}
        {post.deletable ? (
          <button type="button" className="button-danger" onClick={removePost} disabled={actionsBusy}>
            삭제
          </button>
        ) : post.deleteRequestable ? (
          <>
            <span style={{ alignSelf: "center", color: "var(--muted-2)", fontSize: "0.9rem" }}>
              {post.deleteBlockedReason}
            </span>
            <Link className="button-secondary" href={communityNewUrl({ board: "INQUIRY", targetPostId: post.id, returnTo: returnUrl })}>
              관리자에게 삭제 요청
            </Link>
          </>
        ) : null}
      </div>

      {actionMessage ? <div className="card" role="status" style={{ marginTop: "1rem" }}>{actionMessage}</div> : null}

      {actionError ? (
        <div className="card" role="alert" style={{ marginTop: "1rem", color: "var(--bad)" }}>{actionError}</div>
      ) : null}

      <section style={{ marginTop: "1.5rem" }}>
        <h2 style={{ fontSize: "1rem", marginBottom: "0.75rem" }}>댓글 {post.commentCount}</h2>

        {post.comments.length === 0 ? (
          <div className="empty-state">첫 댓글을 남겨보세요.</div>
        ) : (
          <div style={{ display: "grid", gap: "0.75rem" }}>
            {post.comments.map((item) => (
              <div key={item.id} className="card">
                <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem" }}>
                  <span style={{ color: "var(--muted-2)", fontSize: "0.85rem" }}>
                    <AuthorBadge author={item.author} board={post.boardType} /> · {formatDate(item.createdAt)}
                  </span>
                  {item.deletable ? (
                    <button
                      type="button"
                      className="button-ghost"
                      onClick={() => removeComment(item.id)}
                      disabled={actionsBusy}
                    >
                      삭제
                    </button>
                  ) : null}
                </div>
                <div style={{ whiteSpace: "pre-wrap", marginTop: "0.4rem" }}>{item.content}</div>
              </div>
            ))}
          </div>
        )}

        {moreError ? <div role="alert" style={{ color: "var(--bad)", marginTop: "0.75rem" }}>{moreError}</div> : null}
        {post.hasMoreComments ? (
          <div className="button-row" style={{ marginTop: "0.75rem" }}>
            <button type="button" className="button-secondary" onClick={loadMoreComments} disabled={actionsBusy}>
              {loadingMore ? "댓글 불러오는 중…" : moreError ? "댓글 다시 불러오기" : "댓글 더 보기"}
            </button>
            <span style={{ alignSelf: "center", color: "var(--muted-2)" }}>{post.comments.length} / {post.commentCount}개 표시</span>
          </div>
        ) : null}

        <form onSubmit={submitComment} style={{ marginTop: "1rem", display: "grid", gap: "0.5rem" }}>
          {!isInquiry ? <p style={{ color: "var(--ink-2)", fontSize: "0.8rem", margin: 0 }}>댓글 작성 시 이 게시판 포인트 2P를 받습니다.</p> : null}
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            rows={3}
            maxLength={2000}
            placeholder="댓글을 입력하세요"
          />
          <div className="button-row">
            <button type="submit" className="button" disabled={actionsBusy || !comment.trim()}>
              댓글 등록
            </button>
          </div>
        </form>
      </section>
    </AppShell>
  );
}
