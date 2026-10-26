"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import type { InquiryStatus, PostDetail } from "@/types/api";

export type InquiryResolution = {
  status: Exclude<InquiryStatus, "OPEN">;
  adminReply: string;
  deleteTargetPost: boolean;
};

type Props = {
  post: PostDetail;
  busy: boolean;
  onResolve: (payload: InquiryResolution) => Promise<boolean>;
};

export function InquiryResolveForm({ post, busy, onResolve }: Props) {
  const [status, setStatus] = useState<InquiryResolution["status"]>(post.inquiryStatus === "REJECTED" ? "REJECTED" : "RESOLVED");
  const [adminReply, setAdminReply] = useState(post.adminReply ?? "");
  const [deleteTargetPost, setDeleteTargetPost] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const removeTarget = status === "RESOLVED" && post.targetPostDeletable && deleteTargetPost;
    if (removeTarget && !window.confirm("문의에 연결된 대상 글을 삭제하고 처리할까요?")) return;
    if (await onResolve({ status, adminReply: adminReply.trim(), deleteTargetPost: removeTarget })) {
      setDeleteTargetPost(false);
    }
  }

  return (
    <form className="card" onSubmit={submit} style={{ marginTop: "1rem" }}>
      <h2 style={{ fontSize: "1rem", marginBottom: "0.75rem" }}>관리자 문의 처리</h2>
      <fieldset disabled={busy} style={{ border: 0, margin: 0, padding: 0, minWidth: 0, display: "grid", gap: "0.75rem" }}>
        <div style={{ display: "grid", gap: "0.35rem" }}>
          <label htmlFor="inquiry-status">처리 결과</label>
          <select id="inquiry-status" value={status} onChange={(event) => {
            setStatus(event.target.value as InquiryResolution["status"]);
            setDeleteTargetPost(false);
          }}>
            <option value="RESOLVED">처리 완료</option>
            <option value="REJECTED">요청 거절</option>
          </select>
        </div>
        <div style={{ display: "grid", gap: "0.35rem" }}>
          <label htmlFor="inquiry-reply">관리자 답변</label>
          <textarea id="inquiry-reply" value={adminReply} onChange={(event) => setAdminReply(event.target.value)} rows={5} maxLength={20000} placeholder="작성자에게 전달할 답변을 입력하세요" />
        </div>
        {post.targetPostDeletable && status === "RESOLVED" ? (
          <label style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <input type="checkbox" checked={deleteTargetPost} onChange={(event) => setDeleteTargetPost(event.target.checked)} />
            연결된 대상 글도 삭제
          </label>
        ) : null}
        {post.targetPostId && !post.targetPostDeletable ? <div style={{ color: "var(--muted-2)" }}>연결된 대상 글은 이미 삭제되었습니다.</div> : null}
        <div className="button-row">
          <button type="submit" className={deleteTargetPost ? "button-danger" : "button"} disabled={busy}>
            {busy ? "처리 중…" : deleteTargetPost ? "대상 글 삭제 및 처리 저장" : "처리 저장"}
          </button>
        </div>
      </fieldset>
    </form>
  );
}