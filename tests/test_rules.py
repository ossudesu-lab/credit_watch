import unittest
from datetime import date, datetime

from watcher.cost import summarize
from watcher.rules import JST, Config, evaluate, last_weekly_boundary

PRICES = {"claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cache_w": 1.25, "cache_r": 0.10}}

CFG = Config(deposit_usd=5.0, deposit_date=date(2026, 9, 1))

# 2026-09-23 は水曜日
WED_NOON = datetime(2026, 9, 23, 12, 0, tzinfo=JST)


def usd(amount: float, model: str = "claude-haiku-4-5", purpose: str = "eval"):
    """$amount ぶんの入力トークンを持つ集計を作る（haiku は $1/100万）。"""
    return summarize([{f"p|{purpose}|{model}|in": int(amount * 1_000_000)}], PRICES)


EMPTY = summarize([], PRICES)

# 週報は別のテストで見るので、それ以外のテストでは「今週はもう送った」ことにしておく
WEEKLY_SENT = {"weekly:2026-09-20"}


def keys(notices):
    return sorted(n.key.split(":")[0] for n in notices)


class TestSurge(unittest.TestCase):
    def test_1ドルちょうどでは鳴らない(self):
        self.assertEqual(keys(evaluate(WED_NOON, usd(1.0), usd(1.0), EMPTY, CFG, WEEKLY_SENT)), [])

    def test_1ドルを超えたら鳴る(self):
        n = evaluate(WED_NOON, usd(1.2), usd(1.2), EMPTY, CFG, WEEKLY_SENT)
        self.assertEqual(keys(n), ["surge"])
        self.assertIn("$1.20", n[0].subject)

    def test_同じ日に二度は鳴らない(self):
        sent = WEEKLY_SENT | {"surge:2026-09-23"}
        self.assertEqual(keys(evaluate(WED_NOON, usd(3.0), usd(3.0), EMPTY, CFG, sent)), [])

    def test_日本時間で日付を切る(self):
        # 日本時間 9/24 の午前1時 = UTC では 9/23 の16時。印は 9/24 で付くべき
        late = datetime(2026, 9, 24, 1, 0, tzinfo=JST)
        n = evaluate(late, usd(1.5), usd(1.5), EMPTY, CFG, WEEKLY_SENT)
        self.assertEqual(n[0].key, "surge:2026-09-24")


class TestLowBalance(unittest.TestCase):
    def test_残り1ドル未満で鳴る(self):
        n = evaluate(WED_NOON, EMPTY, usd(4.2), EMPTY, CFG, WEEKLY_SENT)
        self.assertEqual(keys(n), ["low"])
        self.assertIn("$0.80", n[0].subject)

    def test_残りちょうど1ドルでは鳴らない(self):
        self.assertEqual(keys(evaluate(WED_NOON, EMPTY, usd(4.0), EMPTY, CFG, WEEKLY_SENT)), [])

    def test_急増と残り少は同時に出る(self):
        # 8/19 の事故の型: 1日で一気に使って残りも尽きかける
        n = evaluate(WED_NOON, usd(4.5), usd(4.5), EMPTY, CFG, WEEKLY_SENT)
        self.assertEqual(keys(n), ["low", "surge"])


class TestAnomaly(unittest.TestCase):
    def test_単価不明のモデルがあれば知らせる(self):
        today = summarize([{"p|eval|claude-mystery-9|in": 5_000_000}], PRICES)
        n = evaluate(WED_NOON, today, today, EMPTY, CFG, WEEKLY_SENT)
        # 金額が分からないので急増は鳴らない。だからこそ anomaly で知らせる
        self.assertEqual(keys(n), ["anomaly"])
        self.assertIn("claude-mystery-9", n[0].body)

    def test_読めない記録があれば知らせる(self):
        today = summarize([{"壊れたキー": 1}], PRICES)
        self.assertEqual(keys(evaluate(WED_NOON, today, today, EMPTY, CFG, WEEKLY_SENT)), ["anomaly"])

    def test_急増の本文に単価不明の注意が付く(self):
        today = summarize([{
            "p|eval|claude-haiku-4-5|in": 2_000_000,
            "p|eval|claude-mystery-9|in": 1,
        }], PRICES)
        surge = [x for x in evaluate(WED_NOON, today, today, EMPTY, CFG, WEEKLY_SENT) if x.key.startswith("surge")][0]
        self.assertIn("実際はこれより多い", surge.body)


class TestWeekly(unittest.TestCase):
    def test_境界は直前の日曜20時(self):
        self.assertEqual(last_weekly_boundary(WED_NOON, CFG), datetime(2026, 9, 20, 20, 0, tzinfo=JST))

    def test_日曜20時より前なら前の週の日曜(self):
        sun_morning = datetime(2026, 9, 27, 9, 0, tzinfo=JST)
        self.assertEqual(last_weekly_boundary(sun_morning, CFG).date(), date(2026, 9, 20))

    def test_日曜20時ちょうどからは今週(self):
        sun_20 = datetime(2026, 9, 27, 20, 0, tzinfo=JST)
        self.assertEqual(last_weekly_boundary(sun_20, CFG).date(), date(2026, 9, 27))

    def test_まだ送っていなければ送る(self):
        n = evaluate(WED_NOON, EMPTY, EMPTY, usd(0.3), CFG, set())
        self.assertEqual(keys(n), ["weekly"])
        self.assertEqual(n[0].key, "weekly:2026-09-20")

    def test_見張り番が遅れても取りこぼさない(self):
        # 日曜20時の回が動かず、月曜朝に初めて動いた
        mon = datetime(2026, 9, 28, 7, 0, tzinfo=JST)
        n = evaluate(mon, EMPTY, EMPTY, EMPTY, CFG, {"weekly:2026-09-20"})
        self.assertEqual([x.key for x in n], ["weekly:2026-09-27"])

    def test_印は次の週報まで残る(self):
        n = evaluate(WED_NOON, EMPTY, EMPTY, EMPTY, CFG, set())
        self.assertGreaterEqual(n[0].ttl_seconds, 7 * 24 * 60 * 60)

    def test_使っていない週も週報は送る(self):
        # 週報は生存確認を兼ねるので、0ドルでも止めない
        n = evaluate(WED_NOON, EMPTY, EMPTY, EMPTY, CFG, set())
        self.assertIn("記録なし", n[0].body)


if __name__ == "__main__":
    unittest.main()
