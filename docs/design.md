# 設計メモ（たたき台・2026-09-23）

要件は [requirements.md](requirements.md)。ここでは「どこに何を置くか」を決める。

## 全体の流れ

```
[kaigo_matching 本番 (Vercel)] ─┐
[kaigo_matching eval (GHA)]    ─┼─ 記録係 ──足し込む──▶ [Upstash Redis]
[kaigo_mcp (ローカル)]          ─┘                           │
                                                             ▼ 1時間おきに読む
                                          [credit_watch の見張り番 (GHA)]
                                                             │
                                                             ▼ 条件に当たったら
                                                          [メール]
```

- **記録係**: 各プロジェクトに入れる数十行の小さな部品。Claude を呼んだ直後にトークン数を Redis に足す
- **見張り番**: credit_watch 本体。Redis を読んで金額にし、条件に当たれば通知する

## リポジトリの中身

```
credit_watch/
├── recorder/
│   ├── recorder.js        # 記録係（JS版）… kaigo_matching にコピーして使う
│   └── recorder.py        # 記録係（Python版）… kaigo_mcp にコピーして使う
├── watcher/
│   ├── prices.json        # 単価表（確認日・出典URLつき）
│   ├── cost.py            # トークン数 → ドル
│   ├── rules.py           # 急増・残り少・週報の判定
│   ├── notify.py          # メール送信（--dry-run で送らず画面に出す）
│   └── main.py            # 見張り番の入口
├── tests/                 # 判定と金額計算のテスト（Redis もメールも使わない）
└── .github/workflows/watch.yml
```

### 記録係はパッケージにせず、ファイルをコピーする

npm や PyPI に公開するほどの量ではない（1ファイル数十行）。
Upstash は **HTTP で叩ける**ので、記録係は SDK を入れずに標準の `fetch` / `urllib` だけで書ける。
依存が増えない。コピー先には「元は credit_watch の recorder.js（版: YYYY-MM-DD）」と1行書いておく。

## Redis に置くもの

日付は**日本時間**で区切る（UTCで区切ると朝9時に「1日」がリセットされてしまう）。

| キー | 型 | 中身 |
| --- | --- | --- |
| `cw:day:2026-09-23` | ハッシュ | フィールド `{プロジェクト}\|{用途}\|{モデル}\|{種類}` → トークン数を足し込む |
| `cw:notified:{理由}:{日付}` | 文字列 | 通知済みの印。48時間で自動削除（週報だけは8日。48時間だと同じ週に二度送ってしまう） |

- 種類は `in` / `out` / `cache_w` / `cache_r` と、呼び出し回数の `calls`
- 例: `kaigo_matching|prod|claude-haiku-4-5|in` → 18234
- 1回の記録は**1往復**（HINCRBY を5つまとめて送る）
- 日ごとのハッシュは消さない（1日数十フィールドで、何年ぶんでも無料枠に収まる）

**Redis に入るのは数字とラベルだけ。** プロンプトも応答も、APIキーも入らない。

## 記録係の約束

1. **本番を絶対に落とさない**
   - 例外はすべて握りつぶす。本番の応答はいつも通り返す
   - 1.5秒で打ち切る（Upstash が遅いときに本番を道連れにしない）
   - Vercel では `waitUntil` で書き込みを応答の後ろに回す（応答を待たせず、途中で打ち切られもしない）
2. **設定が無ければ何もしない**
   - `CW_REDIS_URL` などの環境変数が無いときは黙って素通り。手元で試すときや、他人が clone したときに壊れない
3. **用途（本番 / eval）は環境変数で決める**
   - Vercel には `CW_PURPOSE=prod`、eval の実行環境には `CW_PURPOSE=eval`
   - コードの中で「どこから呼ばれたか」を推測しない

## 見張り番の約束

- 1時間おきに、**今日**と**入金日から今日まで**のハッシュを読む
- 金額は `prices.json` で計算。表にないモデルは金額に入れず「単価不明」として別に出す
- 判定の関数（`rules.py`）は Redis を知らない。数字を受け取って「通知するか・何を書くか」を返すだけ
  → テストは手で書いた数字を渡して確かめる（kaigo_mcp の eval と同じ作り方）
- 通知したら `cw:notified:*` に印をつけ、同じ理由は1日1回まで

## 秘密と設定の置き場所

リポジトリは公開なので、**値は全部 GitHub の Secrets / Variables に置く**。

| 名前 | 置き場所 | 中身 |
| --- | --- | --- |
| `CW_REDIS_URL` / `CW_REDIS_TOKEN` | Secrets（各プロジェクトと credit_watch） | Upstash の接続先と鍵 |
| `CW_SMTP_USER` / `CW_SMTP_PASS` | Secrets（credit_watch のみ） | Gmail のアドレスとアプリパスワード |
| `CW_DEPOSIT_USD` / `CW_DEPOSIT_DATE` | Variables（credit_watch のみ） | 入金額と入金日。残高 = 入金額 − 入金日以降の使用額 |
| `CW_USD_JPY` | Variables | 表示用の固定レート |

入金額は秘密ではないが、リポジトリに書くと入金のたびにコミットが要るので Variables に置く。

## まだ決めないこと

- LINE 通知（メールが動いてから）
- 照合の自動化（Admin API が使えないので、週報に「照合用の数字」を載せて手で見比べる）
