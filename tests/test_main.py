import unittest
from datetime import date, datetime

from watcher.main import date_range, run
from watcher.rules import JST, Config

PRICES = {"claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cache_w": 1.25, "cache_r": 0.10}}
CFG = Config(deposit_usd=5.0, deposit_date=date(2026, 9, 20))
WED_NOON = datetime(2026, 9, 23, 12, 0, tzinfo=JST)


class FakeStore:
    def __init__(self, days=None, notified=()):
        self.days = days or {}
        self.marks = {}
        self._notified = set(notified)
        self.read_dates = []

    def read_days(self, dates):
        self.read_dates = list(dates)
        return [self.days.get(d, {}) for d in dates]

    def notified(self, keys):
        return {k for k in keys if k in self._notified or k in self.marks}

    def mark_notified(self, key, ttl):
        self.marks[key] = ttl


class FakeSender:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, subject, body):
        if self.fail:
            raise OSError("smtp down")
        self.sent.append(subject)


def usd(amount):
    return {"p|eval|claude-haiku-4-5|in": str(int(amount * 1_000_000))}


class TestRun(unittest.TestCase):
    def test_送れたら印を付け次の回は送らない(self):
        store = FakeStore({"2026-09-23": usd(1.5)})
        sender = FakeSender()
        self.assertEqual(run(WED_NOON, store, sender, CFG, PRICES), 2)  # 急増と週報
        self.assertIn("surge:2026-09-23", store.marks)
        self.assertEqual(run(WED_NOON, store, FakeSender(), CFG, PRICES), 0)

    def test_送信に失敗したら印を付けない(self):
        # 印を先に付けると、送れなかった通知が二度と送られなくなる
        store = FakeStore({"2026-09-23": usd(1.5)})
        with self.assertRaises(OSError):
            run(WED_NOON, store, FakeSender(fail=True), CFG, PRICES)
        self.assertEqual(store.marks, {})

    def test_dry_runでは印を付けない(self):
        store = FakeStore({"2026-09-23": usd(1.5)})
        sender = FakeSender()
        run(WED_NOON, store, sender, CFG, PRICES, dry_run=True)
        self.assertEqual(len(sender.sent), 2)
        self.assertEqual(store.marks, {})

    def test_残高は入金日から今日までで出す(self):
        # 入金日より前の使用は数えない
        store = FakeStore({
            "2026-09-19": usd(3.0),  # 入金前
            "2026-09-21": usd(2.0),
            "2026-09-22": usd(2.5),
        }, notified={"weekly:2026-09-20"})
        sender = FakeSender()
        run(WED_NOON, store, sender, CFG, PRICES)
        # 5.0 - (2.0 + 2.5) = 0.5 → 残り少。入金前の3.0も数えると負になり件名が変わる
        self.assertEqual(sender.sent, ["[credit_watch] 残りが $0.50（約75円）です"])

    def test_週報は直前の日曜までの7日間(self):
        store = FakeStore({
            "2026-09-13": usd(0.1),  # 範囲外（8日前）
            "2026-09-14": usd(0.2),
            "2026-09-20": usd(0.3),
            "2026-09-21": usd(0.4),  # 境界の後（来週の週報に入る）
        })
        sender = FakeSender()
        run(WED_NOON, store, sender, CFG, PRICES)
        self.assertEqual(sender.sent, ["[credit_watch] 週報 2026-09-20：$0.50（約75円）"])
        self.assertEqual(store.read_dates[0], "2026-09-14")  # 読み込みは週の頭から


class TestDateRange(unittest.TestCase):
    def test_両端を含む(self):
        self.assertEqual(date_range(date(2026, 9, 1), date(2026, 9, 3)),
                         ["2026-09-01", "2026-09-02", "2026-09-03"])

    def test_逆順なら空(self):
        self.assertEqual(date_range(date(2026, 9, 3), date(2026, 9, 1)), [])


class TestFailureLog(unittest.TestCase):
    """見張り番が失敗したときのログ。公開ログなので、手がかりは出すが鍵やURLは出さない。"""

    def _run(self, env):
        import contextlib
        import io
        import os
        from unittest import mock

        from watcher.main import main

        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(err):
            code = main(["--dry-run"])
        return code, err.getvalue()

    BASE = {
        "KV_REST_API_TOKEN": "SECRET-TOKEN",
        "CW_DEPOSIT_USD": "5",
        "CW_DEPOSIT_DATE": "2026-09-01",
    }

    def test_redis形式のURLを入れ間違えたら言い当てる(self):
        code, log = self._run({**self.BASE, "KV_REST_API_URL": "rediss://default:SECRET@x.upstash.io:6379"})
        self.assertEqual(code, 1)
        self.assertIn("redis:// 形式", log)
        self.assertNotIn("SECRET", log)
        self.assertNotIn("upstash.io", log)

    def test_前後の空白や引用符を言い当てる(self):
        for bad in ["https://x.upstash.io\n", ' https://x.upstash.io', '"https://x.upstash.io"']:
            with self.subTest(bad=repr(bad)):
                code, log = self._run({**self.BASE, "KV_REST_API_URL": bad})
                self.assertIn("空白・改行・引用符", log)
                self.assertNotIn("upstash.io", log)

    def test_httpsで始まらなければ言い当てる(self):
        code, log = self._run({**self.BASE, "KV_REST_API_URL": "x.upstash.io"})
        self.assertIn("https:// で始まっていない", log)

    def test_設定が無ければ名前だけ出す(self):
        code, log = self._run({"KV_REST_API_URL": "https://x.upstash.io"})
        self.assertEqual(code, 1)
        self.assertIn("KV_REST_API_TOKEN", log)


if __name__ == "__main__":
    unittest.main()
