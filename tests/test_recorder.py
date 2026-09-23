import contextlib
import io
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "recorder"))

import recorder  # noqa: E402

CASES = json.loads((ROOT / "recorder" / "cases.json").read_text(encoding="utf-8"))["cases"]

ENV = {
    "KV_REST_API_URL": "https://example.upstash.io/",
    "KV_REST_API_TOKEN": "SECRET-TOKEN",
    "CW_PROJECT": "kaigo_mcp",
    "CW_PURPOSE": "eval",
}
USAGE = {"input_tokens": 10, "output_tokens": 2}


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestBuildCommands(unittest.TestCase):
    def test_JS版と同じ答えを作る(self):
        for c in CASES:
            with self.subTest(c["name"]):
                now = datetime.fromisoformat(c["now"].replace("Z", "+00:00"))
                got = recorder.build_commands(c["project"], c["purpose"], c["model"], c["usage"], now)
                self.assertEqual(got, c["commands"])

    def test_SDKのオブジェクトでも受け取れる(self):
        # anthropic SDK の res.usage は dict ではなく属性を持つオブジェクト
        usage = SimpleNamespace(input_tokens=3, output_tokens=4, cache_creation_input_tokens=None,
                                cache_read_input_tokens=None)
        now = datetime.fromisoformat("2026-09-23T03:00:00+00:00")
        cmds = recorder.build_commands("p", "eval", "m", usage, now)
        self.assertEqual([c[2].rsplit("|", 1)[1] for c in cmds], ["in", "out", "calls"])


class TestRecordUsage(unittest.TestCase):
    def test_pipelineにBearerで送る(self):
        sent = []

        def fake(req, timeout):
            sent.append((req, timeout))
            return FakeResponse()

        recorder.record_usage("claude-haiku-4-5", USAGE, env=ENV, urlopen=fake)
        self.assertEqual(len(sent), 1)
        req, timeout = sent[0]
        self.assertEqual(req.full_url, "https://example.upstash.io/pipeline")
        self.assertEqual(req.get_header("Authorization"), "Bearer SECRET-TOKEN")
        self.assertEqual(timeout, 1.5)

    def test_設定が1つでも欠けていれば何もしない(self):
        for missing in ENV:
            with self.subTest(missing=missing):
                sent = []
                env = {**ENV, missing: ""}
                recorder.record_usage("m", USAGE, env=env, urlopen=lambda r, timeout: sent.append(r))
                self.assertEqual(sent, [])

    def test_通信が失敗しても例外を外に出さず鍵もログに出さない(self):
        def boom(req, timeout):
            raise OSError("failed https://example.upstash.io token=SECRET-TOKEN")

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            recorder.record_usage("m", USAGE, env=ENV, urlopen=boom)
        log = err.getvalue()
        self.assertIn("記録に失敗", log)
        self.assertNotIn("SECRET-TOKEN", log)
        self.assertNotIn("upstash.io", log)


if __name__ == "__main__":
    unittest.main()
