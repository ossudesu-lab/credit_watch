"""Upstash Redis の読み書き（REST）。

SDK は使わず urllib だけで叩く。記録係（recorder）と同じ理由で、依存を増やさないため。
ここで起きたエラーはそのまま上に投げる。見張り番が失敗すると GitHub Actions の実行が
赤くなり、GitHub からメールが来る。黙って握りつぶすと、止まったことに誰も気づけない。
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

TIMEOUT_SEC = 10


class Store:
    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/") + "/pipeline"
        self._token = token

    def _pipeline(self, commands: list[list[Any]]) -> list[Any]:
        req = urllib.request.Request(
            self._url,
            data=json.dumps(commands).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
            results = json.loads(res.read())
        errors = [r["error"] for r in results if "error" in r]
        if errors:
            raise RuntimeError(f"Upstash がエラーを返した: {errors[0]}")
        return [r["result"] for r in results]

    def read_days(self, dates: list[str]) -> list[dict[str, str]]:
        """日ごとのハッシュを読む。HGETALL は [名前, 値, 名前, 値, ...] の平らな配列で返る。"""
        if not dates:
            return []
        flat = self._pipeline([["HGETALL", f"cw:day:{d}"] for d in dates])
        return [dict(zip(f[::2], f[1::2])) for f in flat]

    def notified(self, keys: list[str]) -> set[str]:
        """すでに通知した印があるものを返す。"""
        if not keys:
            return set()
        found = self._pipeline([["EXISTS", f"cw:notified:{k}"] for k in keys])
        return {k for k, n in zip(keys, found) if n}

    def mark_notified(self, key: str, ttl_seconds: int) -> None:
        self._pipeline([["SET", f"cw:notified:{key}", "1", "EX", ttl_seconds]])
