const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const source = fs.readFileSync(path.join(__dirname, "../src/lib/community-comments.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const helpers = {};
new Function("exports", compiled.outputText)(helpers);
const { mergeComments, refreshPostComments } = helpers;
function comment(id, createdAt = "2026-10-09T13:00:00Z") { return { id: String(id).padStart(2, "0"), content: String(id), createdAt }; }
function range(from, to) { return Array.from({length: to - from + 1}, (_, i) => comment(i + from)); }
function post(extra = {}) { return { id: "post", comments: range(1, 20), commentCount: 60, nextCommentCursor: "20", hasMoreComments: true, ...extra }; }
function page(items, cursor, total = 60) { return { items, nextCursor: cursor, hasMore: cursor !== null, totalItems: total }; }
const noRead = async () => { throw new Error("Unexpected continuation read"); };

test("overlapping batches deduplicate and update existing entries", () => {
  const merged = mergeComments([comment(1), comment(2)], [{ ...comment(2), content: "updated" }, comment(3)]);
  assert.deepEqual(merged.map(c => c.id), ["01", "02", "03"]);
  assert.equal(merged[1].content, "updated");
});
test("ordering respects offsets, microseconds and ID ties", () => {
  const merged = mergeComments([], [comment("a", "2026-10-09T13:00:00.000002Z"),
    comment("z", "2026-10-09T22:00:00.000001+09:00"), comment("b", "2026-10-09T13:00:00.000002Z")]);
  assert.deepEqual(merged.map(c => c.id), ["0z", "0a", "0b"]);
});
test("refresh revalidates expanded comments and preserves metadata", async () => {
  const calls = [];
  const refreshed = await refreshPostComments(post({ comments: range(1, 40), nextCommentCursor: "40" }), post({ adminReply: "saved" }),
    async cursor => { calls.push(cursor); return page(range(21, 40), "40"); }, noRead);
  assert.deepEqual(calls, ["20"]);
  assert.equal(refreshed.comments.length, 40);
  assert.equal(refreshed.nextCommentCursor, "40");
  assert.equal(refreshed.adminReply, "saved");
});
test("newly posted comment remains visible while unread middle batches stay reachable", async () => {
  const refreshed = await refreshPostComments(post(), post({ commentCount: 61 }), noRead, async ids => { assert.deepEqual(ids, ["61"]); return page([comment(61)], null, 61); }, comment(61));
  assert.equal(refreshed.comments.length, 21);
  assert.equal(refreshed.comments.at(-1).id, "61");
  assert.equal(refreshed.nextCommentCursor, "20");
  assert.equal(refreshed.hasMoreComments, true);
});
test("deleting the boundary comment revalidates its range and advances safely", async () => {
  const refreshed = await refreshPostComments(post({ comments: range(1, 40), nextCommentCursor: "40" }), post({ commentCount: 59 }),
    async () => page([...range(21, 39), comment(41)], "41", 59), noRead);
  assert.equal(refreshed.comments.some(c => c.id === "40"), false);
  assert.equal(refreshed.comments.length, 40);
  assert.equal(refreshed.nextCommentCursor, "41");
});
test("an externally deleted expanded comment disappears even if another comment keeps the count unchanged", async () => {
  const refreshed = await refreshPostComments(post({ comments: range(1, 40), nextCommentCursor: "40" }), post(),
    async () => page(range(21, 41).filter(c => c.id !== "38"), "41"), noRead);
  assert.equal(refreshed.comments.some(c => c.id === "38"), false);
  assert.equal(refreshed.comments.length, 40);
  assert.equal(refreshed.nextCommentCursor, "41");
});
test("fully expanded comments stay expanded after authoritative refresh", async () => {
  const calls = [];
  const refreshed = await refreshPostComments(post({ comments: range(1, 45), commentCount: 45, nextCommentCursor: null, hasMoreComments: false }),
    post({ commentCount: 45 }), async cursor => { calls.push(cursor); return cursor === "20" ? page(range(21, 40), "40", 45) : page(range(41, 45), null, 45); }, noRead);
  assert.deepEqual(calls, ["20", "40"]);
  assert.equal(refreshed.comments.length, 45);
  assert.equal(refreshed.hasMoreComments, false);
  assert.equal(refreshed.nextCommentCursor, null);
});
test("lower counts never allow cached deleted comments to hide unread live comments", async () => {
  const refreshed = await refreshPostComments(post({ comments: range(1, 40), nextCommentCursor: "40" }), post({ commentCount: 30 }),
    async () => page(range(21, 30), null, 30), async () => page([], null, 30));
  assert.equal(refreshed.comments.length, 30);
  assert.equal(refreshed.commentCount, 30);
  assert.equal(refreshed.hasMoreComments, false);
});
test("a different post never inherits expanded comments", async () => {
  const next = post({ id: "other", comments: [comment("new")] });
  assert.equal(await refreshPostComments(post(), next, noRead, noRead), next);
});
test("failed refresh leaves the previous displayed state intact", async () => {
  const previous = post({ comments: range(1, 40), nextCommentCursor: "40" });
  await assert.rejects(refreshPostComments(previous, post(), async () => { throw new Error("network"); }, noRead), /network/);
  assert.equal(previous.comments.length, 40);
  assert.equal(previous.nextCommentCursor, "40");
});
test("a non-advancing cursor cannot cause an endless refresh", async () => {
  await assert.rejects(refreshPostComments(post({ comments: range(1, 40), nextCommentCursor: "40" }), post(),
    async () => page([], "20"), noRead), /Invalid comment continuation/);
});

test("several recently posted comments survive repeated actions without reading the middle", async () => {
  const previous = post({ comments: [...range(1, 20), comment(61)], commentCount: 61 });
  const refreshed = await refreshPostComments(previous, post({ commentCount: 62 }), noRead,
    async ids => { assert.deepEqual(ids, ["61", "62"]); return page([comment(61), comment(62)], null, 62); }, comment(62));
  assert.equal(refreshed.comments.length, 22);
  assert.equal(refreshed.nextCommentCursor, "20");
});
test("a deleted recent comment beyond unread middle batches is removed", async () => {
  const previous = post({ comments: [...range(1, 20), comment(61)], commentCount: 61 });
  const refreshed = await refreshPostComments(previous, post(), noRead,
    async ids => { assert.deepEqual(ids, ["61"]); return page([], null, 60); });
  assert.equal(refreshed.comments.length, 20);
  assert.equal(refreshed.commentCount, 60);
  assert.equal(refreshed.nextCommentCursor, "20");
});
