const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(relative) {
  const source = fs.readFileSync(path.join(__dirname, relative), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  new Function("exports", "require", compiled.outputText)(exports, () => ({}));
  return exports;
}

const nav = load("../src/lib/workspace-navigation.ts");
const view = load("../src/lib/strategy-view.ts");

test("strategy tab links to its own page and is active only there", () => {
  assert.ok(nav.WORKSPACE_TABS.some(tab => tab.id === "strategies" && tab.label === "전략"));
  assert.equal(nav.workspaceTabUrl("strategies"), "/strategies");
  assert.equal(nav.workspaceTabUrl("assets"), "/mypage");
  assert.equal(nav.workspaceTabUrl("home"), "/dashboard");
  assert.equal(nav.workspaceTabUrl("history"), "/dashboard?tab=history");
  assert.equal(nav.activeWorkspaceTab("/strategies", null), "strategies");
  assert.equal(nav.activeWorkspaceTab("/mypage", "history"), "assets");
  assert.equal(nav.activeWorkspaceTab("/dashboard", "watchlist"), "watchlist");
  assert.equal(nav.activeWorkspaceTab("/dashboard", "unknown"), "home");
  assert.equal(nav.activeWorkspaceTab("/community", null), undefined);
});

test("stage labels and tone show when the effective stage is held below the request", () => {
  assert.equal(view.stageLabel("observe"), "관찰");
  assert.equal(view.stageLabel("shadow"), "섀도");
  assert.equal(view.stageLabel("paper"), "PAPER");
  assert.equal(view.stageLabel("odd"), "odd");
  assert.equal(view.effectiveStageTone("shadow", "observe"), "warn");
  assert.equal(view.effectiveStageTone("shadow", "shadow"), "good");
  assert.equal(view.effectiveStageTone("bogus", "shadow"), "neutral");
});

test("missing verdicts read as not evaluated", () => {
  assert.equal(view.verdictLabel(null), "미평가");
  assert.equal(view.verdictLabel("pass"), "통과");
  assert.equal(view.verdictTone("pass"), "good");
  assert.equal(view.verdictTone("fail"), "bad");
  assert.equal(view.verdictTone(null), "neutral");
});

test("stage reasons translate by code prefix and unknown codes stay untranslated", () => {
  assert.match(view.stageReasonText("holdout_not_passed:HF001_hegemony_filters/holdout_f_both:none"), /보류 구간/);
  assert.equal(view.stageReasonText("something_new:x"), null);
});

test("batch status, phase and halts", () => {
  assert.equal(view.batchStatusLabel("PARTIAL"), "일부 체결");
  assert.equal(view.batchStatusTone("HALTED"), "bad");
  assert.equal(view.phaseLabel("OPEN"), "시가 (장전 동시호가)");
  const halts = [{ scope: "GLOBAL", halted: false, reason: null, updatedAt: "x" }, { scope: "u1", halted: true, reason: "r", updatedAt: "y" }];
  assert.deepEqual(view.activeHalts(halts).map(halt => halt.scope), ["u1"]);
  assert.deepEqual(view.activeHalts(undefined), []);
  assert.equal(view.haltScopeLabel("GLOBAL"), "전체");
});

test("signed percentages", () => {
  assert.equal(view.signedPct(1.234), "+1.23%");
  assert.equal(view.signedPct(-0.4), "-0.40%");
  assert.equal(view.signedPct(0), "0.00%");
  assert.equal(view.signedPct(-0.001), "0.00%");
  assert.equal(view.signedPct(null), "—");
  assert.equal(view.valueSign(-2), "down");
});

test("sparkline points span the box and handle short or flat series", () => {
  assert.equal(view.sparklinePoints([100], 100, 20), "");
  assert.equal(view.sparklinePoints([], 100, 20), "");
  assert.equal(view.sparklinePoints([5, 5], 100, 20, 0), "0,10 100,10");
  assert.equal(view.sparklinePoints([0, 10, 5], 100, 20, 0), "0,20 50,0 100,10");
});

test("strategy list calls the screen endpoint and tolerates missing arrays", async () => {
  const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src/lib/api.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, method: init.method ?? "GET", credentials: init.credentials });
    return new Response('{"strategies":null}', { headers: { "content-type": "application/json" } });
  };
  const context = { exports: {}, process: { env: {} }, Headers, Response, AbortController, DOMException,
    setTimeout, clearTimeout, fetch, Error, TypeError };
  vm.runInNewContext(source, context);
  const result = await context.exports.strategyApi.list();
  assert.deepEqual(calls, [{ url: "/api/v1/strategies", method: "GET", credentials: "include" }]);
  assert.equal(result.strategies.length, 0);
  assert.equal(result.halts.length, 0);
});
