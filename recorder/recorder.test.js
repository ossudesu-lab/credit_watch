// 実行: node --test recorder/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildCommands, recordUsage } from "./recorder.js";

const { cases } = JSON.parse(readFileSync(new URL("./cases.json", import.meta.url), "utf8"));

const ENV = {
  KV_REST_API_URL: "https://example.upstash.io/",
  KV_REST_API_TOKEN: "SECRET-TOKEN",
  CW_PROJECT: "kaigo_matching",
  CW_PURPOSE: "prod",
};
const USAGE = { input_tokens: 10, output_tokens: 2 };

for (const c of cases) {
  test(`共通の答え: ${c.name}`, () => {
    assert.deepEqual(buildCommands(c.project, c.purpose, c.model, c.usage, new Date(c.now)), c.commands);
  });
}

test("Upstash の pipeline に Bearer で送る", async () => {
  const calls = [];
  await recordUsage("claude-haiku-4-5", USAGE, {
    env: ENV,
    fetchImpl: async (url, opts) => (calls.push({ url, opts }), { ok: true }),
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://example.upstash.io/pipeline");
  assert.equal(calls[0].opts.headers.Authorization, "Bearer SECRET-TOKEN");
});

test("設定が1つでも欠けていれば何もしない", async () => {
  for (const missing of Object.keys(ENV)) {
    let called = false;
    const env = { ...ENV, [missing]: "" };
    await recordUsage("m", USAGE, { env, fetchImpl: async () => ((called = true), { ok: true }) });
    assert.equal(called, false, `${missing} が無いのに送った`);
  }
});

test("usage が無ければ何もしない", async () => {
  let called = false;
  await recordUsage("m", undefined, { env: ENV, fetchImpl: async () => ((called = true), { ok: true }) });
  assert.equal(called, false);
});

test("通信が例外を投げても、例外を外に出さない", async () => {
  await recordUsage("m", USAGE, {
    env: ENV,
    fetchImpl: async () => {
      throw new TypeError("network down");
    },
  });
});

test("相手が応答しなくても1.5秒前後で打ち切る", async () => {
  const start = Date.now();
  await recordUsage("m", USAGE, {
    env: ENV,
    // abort されるまで永遠に待つ偽物
    fetchImpl: (_url, { signal }) =>
      new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")))),
  });
  const ms = Date.now() - start;
  assert.ok(ms >= 1400 && ms < 2500, `${ms}ms かかった`);
});

test("失敗のログに鍵もURLも出さない", async () => {
  const logged = [];
  const orig = console.warn;
  console.warn = (m) => logged.push(String(m));
  try {
    await recordUsage("m", USAGE, { env: ENV, fetchImpl: async () => ({ ok: false, status: 401 }) });
    await recordUsage("m", USAGE, {
      env: ENV,
      fetchImpl: async () => {
        throw new Error(`failed https://example.upstash.io token=SECRET-TOKEN`);
      },
    });
  } finally {
    console.warn = orig;
  }
  assert.equal(logged.length, 2);
  assert.match(logged[0], /HTTP 401/);
  for (const m of logged) {
    assert.ok(!m.includes("SECRET-TOKEN"), m);
    assert.ok(!m.includes("upstash.io"), m);
  }
});
