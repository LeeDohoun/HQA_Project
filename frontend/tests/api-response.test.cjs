const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const source = fs.readFileSync(path.join(__dirname, "../src/lib/api.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
function client(fetcher) {
  const exported = {};
  new Function("exports", "fetch", "process", compiled.outputText)(exported, fetcher, { env: { NEXT_PUBLIC_API_BASE: "http://localhost:8000", NODE_ENV: "test" } });
  return exported;
}

test("empty success responses never attempt JSON parsing even with JSON content type", async () => {
  for (const status of [204, 205]) {
    const { communityApi } = client(async (url, init) => {
      if (url.endsWith("/auth/csrf")) return Response.json({token:"test-token"});
      assert.equal(init.headers.get("X-CSRF-Token"), "test-token");
      return new Response(null, { status, headers: { "Content-Type": "application/json" } });
    });
    assert.equal(await communityApi.removeComment("comment"), undefined);
    assert.equal(await communityApi.remove("post"), undefined);
  }
});
test("JSON client errors preserve their status and server message", async () => {
  const { communityApi } = client(async url => url.endsWith("/auth/csrf") ? Response.json({token:"test-token"})
    : new Response(JSON.stringify({ message: "Not your comment" }), { status: 403, headers: { "Content-Type": "application/json" } }));
  await assert.rejects(communityApi.removeComment("comment"), error => error.status === 403 && error.message === "Not your comment");
});
test("visible comment requests carry all IDs, credentials, cancellation and mapped fields", async () => {
  const controller = new AbortController();
  const { communityApi } = client(async (url, init) => {
    const parsed = new URL(url);
    assert.equal(parsed.pathname, "/api/v1/community/posts/post%2Fid/comments/visible");
    assert.deepEqual(parsed.searchParams.getAll("ids"), ["first", "second"]);
    assert.equal(init.credentials, "include");
    assert.equal(init.signal.aborted, controller.signal.aborted);
    return new Response(JSON.stringify({ items: [{ id: "first", content: "body", author: { id: "author", user_id: "user", first_name: "first", last_name: "last" }, mine: true, deletable: true, created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:00:00Z" }], total_items: 50, next_cursor: null, has_more: false }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const page = await communityApi.visibleComments("post/id", ["first", "second"], controller.signal);
  assert.equal(page.totalItems, 50);
  assert.equal(page.items[0].author.userId, "user");
  assert.equal(page.items[0].deletable, true);
  assert.equal(page.items[0].createdAt, "2026-10-10T00:00:00Z");
});

test("each mutation refreshes the token and includes the same session and cancellation signal", async () => {
  let issued = 0; const sent = []; const controller = new AbortController();
  // Generic writes use the same transport as every session-authenticated API.
  const { api } = client(async (url, init) => {
    assert.equal(init.credentials, "include"); assert.equal(init.signal.aborted, controller.signal.aborted);
    if (url.endsWith("/auth/csrf")) return Response.json({token: "token-" + ++issued});
    sent.push(init.headers.get("X-CSRF-Token")); return new Response(null,{status:204});
  });
  await api("/api/v1/community/posts/post", {method:"DELETE",signal:controller.signal});
  await api("/api/v1/community/comments/comment", {method:"DELETE",signal:controller.signal});
  assert.deepEqual(sent,["token-1","token-2"]);
});
test("token bootstrap failure prevents the write entirely", async () => {
  const calls = [];
  const { communityApi } = client(async url => { calls.push(url); return Response.json({message:"Session expired"},{status:403}); });
  await assert.rejects(communityApi.remove("post"), error => error.status===403 && error.message==="Session expired");
  assert.equal(calls.length,1); assert.equal(new URL(calls[0]).pathname,"/api/v1/auth/csrf");
});
test("safe reads do not create a token request", async () => {
  const calls = []; const { api } = client(async url => { calls.push(url); return Response.json({ok:true}); });
  assert.deepEqual(await api("/api/v1/community/free"), {ok:true});
  assert.equal(calls.length,1);
});


test("caller cancellation aborts both token bootstrap and mutations", async () => {
  for (const duringBootstrap of [true, false]) {
    const controller = new AbortController();
    const { api } = client(async (url, init) => {
      if (!duringBootstrap && url.endsWith("/auth/csrf")) return Response.json({token:"test-token"});
      controller.abort();
      assert.equal(init.signal.aborted, true);
      throw init.signal.reason;
    });
    await assert.rejects(api("/api/v1/community/posts/post", {method:"DELETE", signal:controller.signal}), error => error.name === "AbortError");
  }
});
