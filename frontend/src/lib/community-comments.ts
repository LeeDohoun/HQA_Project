import type { CommentPage, PostComment, PostDetail } from "@/types/api";

function compareComments(a: PostComment, b: PostComment): number {
  const time = Date.parse(a.createdAt) - Date.parse(b.createdAt);
  if (time) return time;
  // Preserve PostgreSQL timestamp precision beyond JavaScript's milliseconds.
  const fraction = (value: string) => (value.match(/\.(\d+)/)?.[1] ?? "0").padEnd(9, "0");
  const aFraction = fraction(a.createdAt);
  const bFraction = fraction(b.createdAt);
  if (aFraction !== bFraction) return aFraction < bFraction ? -1 : 1;
  return a.id === b.id ? 0 : a.id < b.id ? -1 : 1;
}

export function mergeComments(existing: PostComment[], incoming: PostComment[]): PostComment[] {
  const unique = new Map(existing.map((comment) => [comment.id, comment]));
  for (const comment of incoming) unique.set(comment.id, comment);
  return [...unique.values()].sort(compareComments);
}

/** Revalidate the expanded range so comments deleted by another user disappear. */
export async function refreshPostComments(
  previous: PostDetail | null,
  next: PostDetail,
  readPage: (cursor: string) => Promise<CommentPage>,
  readVisible: (ids: string[]) => Promise<CommentPage>,
  created?: PostComment
): Promise<PostDetail> {
  if (!previous || previous.id !== next.id) return next;
  const boundary = previous.comments.find((comment) => comment.id === previous.nextCommentCursor)
    ?? previous.comments.at(-1);
  let comments = [...next.comments];
  let cursor = next.nextCommentCursor;
  let hasMore = next.hasMoreComments;
  let total = next.commentCount;
  const visited = new Set<string>();
  while (boundary && hasMore && (!comments.length || compareComments(comments[comments.length - 1], boundary) < 0)) {
    if (!cursor || visited.has(cursor)) throw new Error("Invalid comment continuation");
    visited.add(cursor);
    const page = await readPage(cursor);
    comments = mergeComments(comments, page.items);
    cursor = page.nextCursor;
    hasMore = page.hasMore;
    total = page.totalItems;
  }
  // Previously posted comments can be visible beyond the unread middle range.
  // Confirm these individually without downloading every intervening page.
  const last = comments.at(-1);
  const outlying = mergeComments(previous.comments, created ? [created] : [])
    .filter((comment) => !last || compareComments(comment, last) > 0);
  for (let offset = 0; offset < outlying.length; offset += 50) {
    const checked = await readVisible(outlying.slice(offset, offset + 50).map((comment) => comment.id));
    comments = mergeComments(comments, checked.items);
    total = checked.totalItems;
  }
  return { ...next, comments, commentCount: total, hasMoreComments: hasMore, nextCommentCursor: hasMore ? cursor : null };
}
