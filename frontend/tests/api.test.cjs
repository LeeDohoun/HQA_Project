const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const source = ts.transpileModule(fs.readFileSync(`${__dirname}/../src/lib/api.ts`, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;
function client(fetch) {
  const context = { exports: {}, process: { env: {} }, Headers, Response, AbortController, DOMException,
    setTimeout, clearTimeout, fetch, Error, TypeError };
  vm.runInNewContext(source, context);
  return context.exports.api;
}
test('uses same-origin proxy and session credentials by default', async () => {
  const api = client(async (url, init) => {
    assert.equal(url, '/api/v1/auth/me');
    assert.equal(init.credentials, 'include');
    return new Response('{"ok":true}', { headers: { 'content-type': 'application/json' } });
  });
  assert.equal((await api('/api/v1/auth/me')).ok, true);
});
test('accepts empty 204 responses', async () => {
  assert.equal(await client(async () => new Response(null, { status: 204 }))('/api/item'), undefined);
});
test('preserves nested backend validation messages and status', async () => {
  const api = client(async () => new Response('{"detail":{"message":"입력 오류"}}', {
    status: 400, headers: { 'content-type': 'application/json' }
  }));
  await assert.rejects(api('/api/item'), error => error.status === 400 && error.message === '입력 오류');
});
test('does not present proxy HTML as an error message', async () => {
  const api = client(async () => new Response('<html>private detail</html>', { status: 502 }));
  await assert.rejects(api('/api/item'), error => error.status === 502 && !error.message.includes('private'));
});
test('reports malformed JSON clearly', async () => {
  const api = client(async () => new Response('not-json', { headers: { 'content-type': 'application/json' } }));
  await assert.rejects(api('/api/item'), /서버 응답을 읽을 수 없습니다/);
});
test('preserves caller cancellation and does not retry writes', async () => {
  let calls = 0;
  const controller = new AbortController();
  controller.abort();
  const api = client(async (_, init) => { calls++; throw init.signal.reason; });
  await assert.rejects(api('/api/order', { method: 'POST', signal: controller.signal }), error => error.name === 'AbortError');
  assert.equal(calls, 1);
});
