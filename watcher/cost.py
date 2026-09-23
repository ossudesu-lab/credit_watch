"""記録（トークン数）を金額にする。

Redis の日ごとハッシュは `{プロジェクト}|{用途}|{モデル}|{種類}` → 数 の形で入っている。
この関数群は Redis を知らない。ハッシュの中身（dict）を受け取って計算するだけ。

**単価表に無いモデルは0円にしない。** 金額に入れず、トークン数だけ別に数えて返す。
黙って0円にすると、使いすぎているのに「使っていない」と見えてしまうため。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PRICES_PATH = Path(__file__).resolve().parent / "prices.json"

# 金額になる種類。calls（呼び出し回数）は金額には関係しない。
PRICED_KINDS = ("in", "out", "cache_w", "cache_r")
KINDS = PRICED_KINDS + ("calls",)

# API が返すモデル名には日付が付くことがある（例: claude-haiku-4-5-20251001）。
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def load_prices(path: Path = PRICES_PATH) -> dict[str, dict[str, float]]:
    return json.loads(path.read_text(encoding="utf-8"))["models"]


def normalize_model(model: str) -> str:
    return _DATE_SUFFIX.sub("", model)


@dataclass
class Line:
    """プロジェクト×用途×モデル 1行ぶんの集計。"""

    project: str
    purpose: str
    model: str
    tokens: dict[str, int] = field(default_factory=dict)
    usd: float | None = None  # 単価不明なら None

    @property
    def calls(self) -> int:
        return self.tokens.get("calls", 0)


@dataclass
class Summary:
    lines: list[Line]
    unreadable: list[str]  # 形式が読めなかったフィールド名

    @property
    def total_usd(self) -> float:
        """単価が分かっている分だけの合計。単価不明の分は含まない。"""
        return sum(l.usd for l in self.lines if l.usd is not None)

    @property
    def unknown_models(self) -> list[str]:
        return sorted({l.model for l in self.lines if l.usd is None})


def summarize(days: list[dict[str, int | str]], prices: dict[str, dict[str, float]]) -> Summary:
    """日ごとのハッシュ（複数日）をまとめて、行ごとの金額を出す。

    Redis から来る値は文字列のこともあるので int にしてから足す。
    """
    merged: dict[tuple[str, str, str], dict[str, int]] = {}
    unreadable: list[str] = []

    for day in days:
        for key, raw in day.items():
            parts = key.split("|")
            if len(parts) != 4 or parts[3] not in KINDS:
                unreadable.append(key)
                continue
            project, purpose, model, kind = parts
            try:
                n = int(raw)
            except (TypeError, ValueError):
                unreadable.append(key)
                continue
            bucket = merged.setdefault((project, purpose, normalize_model(model)), {})
            bucket[kind] = bucket.get(kind, 0) + n

    lines = []
    for (project, purpose, model), tokens in sorted(merged.items()):
        price = prices.get(model)
        usd = None
        if price is not None:
            usd = sum(tokens.get(k, 0) * price[k] for k in PRICED_KINDS) / 1_000_000
        lines.append(Line(project, purpose, model, tokens, usd))

    return Summary(lines, unreadable)
