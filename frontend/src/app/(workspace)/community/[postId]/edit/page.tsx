"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { AppShell } from "@/components/common/app-shell";
import { communityApi } from "@/lib/api";
import { communityPostUrl, communityReturnUrl } from "@/lib/community-navigation";
import type { PostDetail } from "@/types/api";

export default function EditPostPage() {
  const router = useRouter();
  const { postId } = useParams<{ postId: string }>();
  const searchParams = useSearchParams();
  const [post, setPost] = useState<PostDetail | null>(null);
  const detailUrl = communityPostUrl(postId, communityReturnUrl(searchParams, post?.boardType ?? "FREE"));
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setPost(null);
    setError(null);
    void communityApi.get(postId).then((data) => {
      if (!active) return;
      setPost(data);
      setTitle(data.title);
      setContent(data.content);
    }).catch((err: unknown) => {
      if (active) setError(err instanceof Error ? err.message : "글을 불러오지 못했습니다.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [postId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || !post?.editable || !title.trim() || !content.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await communityApi.update(postId, { title: title.trim(), content });
      router.push(detailUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : "수정 내용을 저장하지 못했습니다.");
      setSubmitting(false);
    }
  }

  return (
    <AppShell title="글 수정" actions={<Link className="button-ghost" href={detailUrl}>글로 돌아가기</Link>}>
      {loading ? <div className="card">불러오는 중…</div> : !post ? (
        <div className="card" role="alert" style={{ color: "var(--bad)" }}>{error ?? "글을 찾을 수 없습니다."}</div>
      ) : !post.editable ? (
        <div className="card" role="alert">본인이 작성한 글만 수정할 수 있습니다.</div>
      ) : (
        <form className="card" onSubmit={submit}>
          <fieldset disabled={submitting} style={{ border: 0, padding: 0, margin: 0, minWidth: 0, display: "grid", gap: "1rem" }}>
            {post.stockCode ? <div style={{ color: "var(--muted-2)" }}>종목코드: {post.stockCode}</div> : null}
            <div style={{ display: "grid", gap: "0.35rem" }}>
              <label htmlFor="edit-title">제목</label>
              <input id="edit-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={160} required />
            </div>
            <div style={{ display: "grid", gap: "0.35rem" }}>
              <label htmlFor="edit-content">내용</label>
              <textarea id="edit-content" value={content} onChange={(event) => setContent(event.target.value)} rows={14} maxLength={20000} required />
            </div>
            {error ? <div role="alert" style={{ color: "var(--bad)" }}>{error}</div> : null}
            <div className="button-row">
              <button type="submit" className="button" disabled={submitting || !title.trim() || !content.trim()}>
                {submitting ? "저장 중…" : "수정 저장"}
              </button>
              <button type="button" className="button-secondary" onClick={() => router.push(detailUrl)}>취소</button>
            </div>
          </fieldset>
        </form>
      )}
    </AppShell>
  );
}