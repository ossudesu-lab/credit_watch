import unittest

from watcher.cost import load_prices, normalize_model, summarize

PRICES = {
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cache_w": 1.25, "cache_r": 0.10},
}


class TestSummarize(unittest.TestCase):
    def test_金額は4種類のトークンから出す(self):
        s = summarize([{
            "kaigo_matching|prod|claude-haiku-4-5|in": 1_000_000,
            "kaigo_matching|prod|claude-haiku-4-5|out": 100_000,
            "kaigo_matching|prod|claude-haiku-4-5|cache_w": 1_000_000,
            "kaigo_matching|prod|claude-haiku-4-5|cache_r": 1_000_000,
            "kaigo_matching|prod|claude-haiku-4-5|calls": 7,
        }], PRICES)
        # 1.0 + 0.5 + 1.25 + 0.10
        self.assertAlmostEqual(s.total_usd, 2.85)
        self.assertEqual(s.lines[0].calls, 7)

    def test_複数日を足し合わせる(self):
        day1 = {"p|eval|claude-haiku-4-5|in": 500_000}
        day2 = {"p|eval|claude-haiku-4-5|in": 500_000}
        s = summarize([day1, day2], PRICES)
        self.assertEqual(len(s.lines), 1)
        self.assertAlmostEqual(s.total_usd, 1.0)

    def test_Redisから文字列で来ても足せる(self):
        s = summarize([{"p|eval|claude-haiku-4-5|in": "1000000"}], PRICES)
        self.assertAlmostEqual(s.total_usd, 1.0)

    def test_用途が違えば別の行(self):
        s = summarize([{
            "p|prod|claude-haiku-4-5|in": 1_000_000,
            "p|eval|claude-haiku-4-5|in": 2_000_000,
        }], PRICES)
        by_purpose = {l.purpose: l.usd for l in s.lines}
        self.assertAlmostEqual(by_purpose["prod"], 1.0)
        self.assertAlmostEqual(by_purpose["eval"], 2.0)

    def test_単価不明のモデルは0円にせず別に数える(self):
        s = summarize([{
            "p|eval|claude-haiku-4-5|in": 1_000_000,
            "p|eval|claude-mystery-9|in": 1_000_000,
        }], PRICES)
        self.assertAlmostEqual(s.total_usd, 1.0)
        self.assertEqual(s.unknown_models, ["claude-mystery-9"])
        unknown = [l for l in s.lines if l.model == "claude-mystery-9"][0]
        self.assertIsNone(unknown.usd)

    def test_日付つきのモデル名も同じモデルとして扱う(self):
        s = summarize([{
            "p|prod|claude-haiku-4-5-20251001|in": 1_000_000,
            "p|prod|claude-haiku-4-5|in": 1_000_000,
        }], PRICES)
        self.assertEqual(len(s.lines), 1)
        self.assertAlmostEqual(s.total_usd, 2.0)

    def test_読めない記録は捨てずに報告する(self):
        s = summarize([{
            "壊れたキー": 1,
            "p|prod|claude-haiku-4-5|unknown_kind": 1,
            "p|prod|claude-haiku-4-5|in": "abc",
        }], PRICES)
        self.assertEqual(len(s.unreadable), 3)
        self.assertEqual(s.lines, [])

    def test_記録が無ければ0ドル(self):
        s = summarize([], PRICES)
        self.assertEqual(s.total_usd, 0)


class TestPrices(unittest.TestCase):
    def test_本物の単価表が読めて必要な種類が揃っている(self):
        prices = load_prices()
        self.assertIn("claude-sonnet-4-6", prices)  # 本番の連絡文で使っている
        for model, p in prices.items():
            with self.subTest(model=model):
                self.assertEqual(set(p), {"in", "out", "cache_w", "cache_r"})

    def test_normalize_model(self):
        self.assertEqual(normalize_model("claude-haiku-4-5-20251001"), "claude-haiku-4-5")
        self.assertEqual(normalize_model("claude-opus-5-5"), "claude-opus-5-5")


if __name__ == "__main__":
    unittest.main()
