// credit_watch の記録係（JS版）。
// 元: credit_watch/recorder/recorder.js（版: 2026-09-23）。各プロジェクトにはこのファイルをコピーして使う。
//
// Claude を呼んだ直後に、API が返したトークン数を Upstash Redis に足し込む。
// 記録するのは数字とラベルだけ。プロンプトも応答も、APIキーも送らない。
//
// 約束:
// 1. 本番を絶対に落とさない。recordUsage は決して例外を投げず、1.5秒で打ち切る
// 2. 設定（環境変数）が無ければ何もしない
// 3. 用途（prod / eval）は環境変数 CW_PURPOSE で決める。呼ばれ方から推測しない

const TIMEOUT_MS = 1500;

// API の usage のフィールド名 → 記録の種類
const KINDS = [
  ["input_tokens", "in"],
  ["output_tokens", "out"],
  ["cache_creation_input_tokens", "cache_w"],
  ["cache_read_input_tokens", "cache_r"],
];

// 日本時間の日付（YYYY-MM-DD）。UTC で切ると朝9時に日付が変わってしまう。
export function dayJST(now) {
  return new Date(now.getTime() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

// 区切り文字 | がラベルに入ると列がずれるので置き換える
function label(s) {
  return String(s).replaceAll("|", "_");
}

export function buildCommands(project, purpose, model, usage, now) {
  const key = `cw:day:${dayJST(now)}`;
  const prefix = [project, purpose, model].map(label).join("|");
  const cmds = [];
  for (const [src, kind] of KINDS) {
    const n = Number(usage?.[src] ?? 0);
    if (Number.isFinite(n) && n > 0) cmds.push(["HINCRBY", key, `${prefix}|${kind}`, Math.trunc(n)]);
  }
  cmds.push(["HINCRBY", key, `${prefix}|calls`, 1]);
  return cmds;
}

// 記録する。戻り値の Promise は必ず成功で終わる（失敗しても reject しない）。
// Vercel では応答を返すと処理が打ち切られることがあるので、呼び出し側で waitUntil に渡すこと。
export async function recordUsage(model, usage, { env = process.env, fetchImpl = fetch, now = new Date() } = {}) {
  try {
    const { CW_REDIS_URL: url, CW_REDIS_TOKEN: token, CW_PROJECT: project, CW_PURPOSE: purpose } = env;
    if (!url || !token || !project || !purpose || !usage) return;

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    try {
      const res = await fetchImpl(`${url.replace(/\/$/, "")}/pipeline`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(buildCommands(project, purpose, model, usage, now)),
        signal: ctrl.signal,
      });
      // fetch は 401 などでは例外を投げないので、ここで拾う
      if (!res.ok) warn(`HTTP ${res.status}`);
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    // 記録の失敗で本番を止めない。抜けた分は週報の照合で気づく
    warn(e?.name ?? "error");
  }
}

// 失敗を1行だけログに残す。鍵やURLは出さない（公開リポジトリの Actions ログは誰でも読める）
function warn(reason) {
  try {
    console.warn(`[credit_watch] 記録に失敗: ${reason}`);
  } catch {
    // ログすら出せなくても本番は止めない
  }
}
