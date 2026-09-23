"""通知するかどうかを決める。

この関数群は Redis もメールも知らない。集計済みの数字と「もう通知した印」を受け取り、
送るべき通知の一覧を返すだけ。だからテストは手で書いた数字を渡して確かめられる。

通知の種類（requirements.md「通知の条件」）:
- surge   … 今日の使用額がしきい値を超えた
- low     … 残りがしきい値を切った
- anomaly … 単価不明のモデル、または読めない記録がある（金額が実際より少なく出ている恐れ）
- weekly  … 週報。生存確認も兼ねる
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .cost import Summary

JST = timezone(timedelta(hours=9))

DAY = 24 * 60 * 60


@dataclass
class Config:
    deposit_usd: float
    deposit_date: date
    daily_limit_usd: float = 1.0
    low_balance_usd: float = 1.0
    usd_jpy: float = 150.0
    weekly_weekday: int = 6  # 月曜=0 … 日曜=6
    weekly_hour: int = 20


@dataclass
class Notice:
    key: str  # 通知済みの印の名前。同じ key は二度送らない
    ttl_seconds: int  # 印を残す長さ
    subject: str
    body: str


def jst_today(now: datetime) -> date:
    return now.astimezone(JST).date()


def last_weekly_boundary(now: datetime, cfg: Config) -> datetime:
    """直近の「週報を出す時刻」（今より前で一番新しいもの）。

    その時刻ちょうどに見張り番が動くとは限らない（GitHub Actions は遅れる）。
    境界を過ぎて最初に動いた回で送れば、遅れても取りこぼさない。
    """
    now = now.astimezone(JST)
    days_back = (now.weekday() - cfg.weekly_weekday) % 7
    b = now.replace(hour=cfg.weekly_hour, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    if b > now:
        b -= timedelta(days=7)
    return b


def _money(usd: float, cfg: Config) -> str:
    return f"${usd:.2f}（約{usd * cfg.usd_jpy:,.0f}円）"


def _unknown_note(s: Summary) -> str:
    if not s.unknown_models:
        return ""
    return "\n※ 単価不明のモデルの分は含んでいません。実際はこれより多いです: " + ", ".join(s.unknown_models)


def _breakdown(s: Summary, cfg: Config) -> str:
    rows = sorted(s.lines, key=lambda l: -1 if l.usd is None else l.usd, reverse=True)
    out = []
    for l in rows:
        money = "単価不明" if l.usd is None else _money(l.usd, cfg)
        out.append(f"- {l.project} / {l.purpose} / {l.model}: {money}（{l.calls}回）")
    return "\n".join(out) if out else "- 記録なし"


def evaluate(
    now: datetime,
    today: Summary,
    since_deposit: Summary,
    week: Summary,
    cfg: Config,
    notified: set[str],
) -> list[Notice]:
    d = jst_today(now)
    remaining = cfg.deposit_usd - since_deposit.total_usd
    notices: list[Notice] = []

    if today.total_usd > cfg.daily_limit_usd:
        notices.append(Notice(
            key=f"surge:{d}",
            ttl_seconds=2 * DAY,
            subject=f"[credit_watch] 今日の使用額が {_money(today.total_usd, cfg)} を超えました",
            body=(
                f"今日（{d}）の使用額: {_money(today.total_usd, cfg)}"
                f"（しきい値 ${cfg.daily_limit_usd:.2f}）\n"
                f"残り（推定）: {_money(remaining, cfg)}\n\n"
                f"今日の内訳:\n{_breakdown(today, cfg)}"
                f"{_unknown_note(today)}"
            ),
        ))

    if remaining < cfg.low_balance_usd:
        notices.append(Notice(
            key=f"low:{d}",
            ttl_seconds=2 * DAY,
            subject=f"[credit_watch] 残りが {_money(remaining, cfg)} です",
            body=(
                f"入金 ${cfg.deposit_usd:.2f}（{cfg.deposit_date}）のうち、"
                f"{_money(since_deposit.total_usd, cfg)} を使いました。\n"
                f"残り（推定）: {_money(remaining, cfg)}\n"
                f"正確な残高は Console で確認してください。"
                f"{_unknown_note(since_deposit)}"
            ),
        ))

    if today.unknown_models or today.unreadable:
        parts = []
        if today.unknown_models:
            parts.append("単価表に無いモデル: " + ", ".join(today.unknown_models)
                         + "\n→ watcher/prices.json に追加してください。")
        if today.unreadable:
            parts.append(f"読めない記録: {len(today.unreadable)}件（例: {today.unreadable[0]}）"
                         + "\n→ 記録係の版が古い可能性があります。")
        notices.append(Notice(
            key=f"anomaly:{d}",
            ttl_seconds=2 * DAY,
            subject="[credit_watch] 金額を正しく出せない記録があります",
            body="\n\n".join(parts) + "\n\nこの分は金額に入っていないため、使用額は実際より少なく出ています。",
        ))

    boundary = last_weekly_boundary(now, cfg)
    weekly_key = f"weekly:{boundary.date()}"
    if weekly_key not in notified:
        notices.append(Notice(
            key=weekly_key,
            # 次の週報まで印を残す。短いと同じ週に二度送ってしまう
            ttl_seconds=8 * DAY,
            subject=f"[credit_watch] 週報 {boundary.date()}：{_money(week.total_usd, cfg)}",
            body=(
                f"この1週間の使用額: {_money(week.total_usd, cfg)}\n"
                f"残り（推定）: {_money(remaining, cfg)}\n\n"
                f"内訳:\n{_breakdown(week, cfg)}"
                f"{_unknown_note(week)}\n\n"
                "照合: Console の使用量ページを API キー別に絞り込み、上の数字と大きくずれていないか確認してください。\n"
                "この週報が届かない週は、見張り番が止まっています。"
            ),
        ))

    return [n for n in notices if n.key not in notified]
