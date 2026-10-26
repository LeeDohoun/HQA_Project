const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

// Use the project's compiler to load this pure helper without another test framework.
const source = fs.readFileSync(path.join(__dirname, "../src/lib/community-navigation.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
const navigation = {};
new Function("exports", compiled.outputText)(navigation);
const { readCommunityListState, communityListUrl, communityReturnUrl, communityPostUrl, communityEditUrl, communityNewUrl } = navigation;

function params(query) { return new URLSearchParams(query); }

test("stock filter and zero-based page survive URL round trip", () => {
  const state = { board: "STOCK", stockCode: "005930", page: 2 };
  assert.equal(communityListUrl(state), "/community?board=STOCK&stockCode=005930&page=2");
  assert.deepEqual(readCommunityListState(params(communityListUrl(state).split("?")[1])), state);
});

test("invalid pages never reach the backend's integer page parameter", () => {
  for (const page of ["-1", "1.2", "NaN", "1e3", "Infinity", "2147483648", "9999999999999999999999", ""]) {
    assert.equal(readCommunityListState(params({ board: "FREE", page })).page, 0, page);
  }
  assert.equal(readCommunityListState(params({ page: "2147483647" })).page, 2147483647);
});

test("unknown board defaults to free and other boards discard stock filters", () => {
  assert.deepEqual(readCommunityListState(params({ board: "unknown", stockCode: "005930" })), { board: "FREE", stockCode: "", page: 0 });
  assert.deepEqual(readCommunityListState(params({ board: "INQUIRY", stockCode: "005930", page: "1" })), { board: "INQUIRY", stockCode: "", page: 1 });
});

test("return destination rejects external and non-list URLs", () => {
  for (const returnTo of ["https://example.com/community", "//example.com/community", "javascript:alert(1)", "/login", "/community/post", "/community/../login"]) {
    assert.equal(communityReturnUrl(params({ returnTo }), "STOCK"), "/community?board=STOCK", returnTo);
  }
});

test("return destination normalizes malformed state and drops unrelated parameters", () => {
  const returnTo = "/community?board=STOCK&stockCode=%20005930%20&page=-1&returnTo=https://example.com&extra=1#top";
  assert.equal(communityReturnUrl(params({ returnTo })), "/community?board=STOCK&stockCode=005930");
});

test("detail and edit links preserve the full list without splitting nested query parameters", () => {
  const returnTo = "/community?board=STOCK&stockCode=005930&page=2";
  for (const href of [communityPostUrl("post/id", returnTo), communityEditUrl("post/id", returnTo)]) {
    const url = new URL(href, "https://community.invalid");
    assert.match(url.pathname, /post%2Fid/);
    assert.equal(communityReturnUrl(url.searchParams), returnTo);
    assert.equal(url.searchParams.has("page"), false);
  }
});

test("stock writing carries selected stock and source page", () => {
  const returnTo = "/community?board=STOCK&stockCode=005930&page=2";
  const url = new URL(communityNewUrl({ board: "STOCK", stockCode: "005930", returnTo }), "https://community.invalid");
  assert.equal(url.searchParams.get("stockCode"), "005930");
  assert.equal(communityReturnUrl(url.searchParams), returnTo);
});

test("deletion inquiry carries target and original list, without a stock field", () => {
  const returnTo = "/community?board=STOCK&stockCode=005930&page=2";
  const url = new URL(communityNewUrl({ board: "INQUIRY", stockCode: "005930", targetPostId: "post", returnTo }), "https://community.invalid");
  assert.equal(url.searchParams.get("targetPostId"), "post");
  assert.equal(url.searchParams.has("stockCode"), false);
  assert.equal(communityReturnUrl(url.searchParams), returnTo);
});