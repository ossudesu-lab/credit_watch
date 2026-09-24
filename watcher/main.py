"""見張り番の入口。GitHub Actions から1時間おきに呼ばれる。

    python -m watcher.main            # 本番（条件に当たればメールを送る）
    python -m watcher.main --dry-run  # 送らず画面に出す。印も付けない

**公開リポジトリの Actions のログは誰でも読める。** 本番の実行では金額も内訳も出さず、
「何件送ったか」だけを出す。鍵・URL・Upstash の生の応答も出さない。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

from .cost import load_prices, summarize
from .notify import GmailSender, PrintSender
from .rules import JST, Config, evaluate, jst_today, last_weekly_boundary


def date_range(start: date, end: date) -> list[str]:
    """start から end まで（両端を含む）の日付。start が end より後なら空。"""
    days = (end - start).days
    return [(start + timedelta(days=i)).isoformat() for i in range(days + 1)] if days >= 0 else []


def load_config(env) -> Config:
    return Config(
        deposit_usd=float(env["CW_DEPOSIT_USD"]),
        deposit_date=date.fromisoformat(env["CW_DEPOSIT_DATE"]),
        usd_jpy=float(env.get("CW_USD_JPY") or 150),
    )


def run(now: datetime, store, sender, cfg: Config, prices, dry_run: bool = False) -> int:
    """1回ぶんの見張り。送った件数を返す。

    印は**送れた後に**付ける。送る前に付けると、送信に失敗したとき二度と送られなくなる。
    """
    today = jst_today(now)
    week_end = last_weekly_boundary(now, cfg).date()

    # 入金日からの分はまとめて読み、今日と週の分はそこから切り出す（読み込みを1回で済ませる）
    first = min(cfg.deposit_date, week_end - timedelta(days=6))
    all_dates = date_range(first, today)
    by_date = dict(zip(all_dates, store.read_days(all_dates)))

    def pick(start: date, end: date):
        return [by_date[d] for d in date_range(start, end) if d in by_date]

    s_today = summarize(pick(today, today), prices)
    s_since = summarize(pick(cfg.deposit_date, today), prices)
    s_week = summarize(pick(week_end - timedelta(days=6), week_end), prices)

    candidates = [f"surge:{today}", f"low:{today}", f"anomaly:{today}", f"weekly:{week_end}"]
    notices = evaluate(now, s_today, s_since, s_week, cfg, store.notified(candidates))

    for n in notices:
        sender.send(n.subject, n.body)
        if not dry_run:
            store.mark_notified(n.key, n.ttl_seconds)
    return len(notices)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    env = os.environ

    from .store import Store  # テストで main を読むときにネットワークの準備をしないよう、ここで読む

    store = Store(env["KV_REST_API_URL"], env["KV_REST_API_TOKEN"])
    if args.dry_run:
        sender = PrintSender()
    else:
        user = env["CW_SMTP_USER"]
        sender = GmailSender(user, env["CW_SMTP_PASS"], env.get("CW_MAIL_TO") or user)

    try:
        sent = run(datetime.now(JST), store, sender, load_config(env), load_prices(), dry_run=args.dry_run)
    except Exception as e:
        # 例外の文面やトレースバックは公開ログに出さない（応答の中身が混ざりうるため）。
        # 種類だけ出して失敗で終わる。原因の調査は手元で --dry-run を使う
        print(f"見張り失敗: {type(e).__name__}", file=sys.stderr)
        return 1
    print(f"見張り完了: 通知 {sent} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
