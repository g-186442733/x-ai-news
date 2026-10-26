from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FETCH = ROOT / "scripts" / "fetch_via_snapshot.py"
REAL_SNAPSHOT = ROOT / "data" / "latest.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def post(
    *,
    text: str = "A real post",
    username: str = "alice",
    name: str = "Alice",
    created_at: str = "2026-10-24T10:00:00Z",
    url: str = "https://x.com/alice/status/1111111111111111111",
    like_count=1,
    retweet_count=0,
    lang: str = "en",
) -> dict:
    record = {
        "author_name": name,
        "author_username": username,
        "text": text,
        "created_at": created_at,
        "like_count": like_count,
        "retweet_count": retweet_count,
        "url": url,
        "lang": lang,
    }
    return record


def news(
    *,
    record_id: str = "2114363022773002376",
    name: str = "A news item",
    summary: str = "A summary",
    url: str = "https://x.com/i/trending/2114363022773002376",
    updated_at: str = "2026-10-25T17:14:32Z",
    topics=None,
) -> dict:
    return {
        "id": record_id,
        "name": name,
        "summary": summary,
        "url": url,
        "updated_at": updated_at,
        "topics": ["AI", "Technology"] if topics is None else topics,
    }


def base_snapshot() -> dict:
    return {
        "generated_at": "2026-10-26T02:05:34+08:00",
        "timezone": "Asia/Shanghai",
        "source": "local test snapshot",
        "trends_note": "No clear AI trends in the global list.",
        "posts_zh": [
            post(
                text="中文帖子",
                username="zh_author",
                name="中文作者",
                created_at="2026-10-25T09:00:00+08:00",
                url="https://x.com/zh_author/status/2222222222222222222",
                lang="zh",
            )
        ],
        "posts_en": [
            post(
                text="English post",
                username="en_author",
                name="English Author",
                created_at="2026-10-24T10:00:00Z",
                url="https://x.com/en_author/status/3333333333333333333",
            )
        ],
        "news": [news()],
        "trends_ai": [],
        "grokbot_posts": [
            post(
                text="Grok Bot post",
                username="grokfan",
                name="Grok Fan",
                created_at="2026-10-20T10:00:00Z",
                url="https://x.com/grokfan/status/4444444444444444444",
            )
        ],
        "grokbot_intro": {
            "zh": {"title": "Grok Bot 是什么", "bullets": ["第一点", "第二点"]},
            "en": {"title": "What is Grok Bot", "bullets": ["First", "Second"]},
        },
    }


class FetchSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_snapshot(self, snapshot, name: str = "latest.json") -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def run_fetch(self, snapshot_path: Path, out_dir: Path | None = None, check: bool = False):
        cmd = [sys.executable, str(FETCH), "--snapshot", str(snapshot_path)]
        if out_dir is not None:
            cmd.extend(["--out-dir", str(out_dir)])
        if check:
            cmd.append("--check")
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)

    def read_json(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_real_snapshot_check_is_read_only(self) -> None:
        if not REAL_SNAPSHOT.is_file():
            self.skipTest("real snapshot not present")
        before = REAL_SNAPSHOT.read_bytes()
        result = self.run_fetch(REAL_SNAPSHOT, check=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check ok", result.stdout)
        self.assertEqual(before, REAL_SNAPSHOT.read_bytes())

    def test_normalizes_contract_and_writes_hashes(self) -> None:
        snapshot = base_snapshot()
        snapshot["posts_zh"].append(
            post(
                text="更新的中文帖子",
                username="zh_author",
                name="中文作者",
                created_at="2026-10-25T10:00:00+08:00",
                url="https://x.com/zh_author/status/5555555555555555555",
                lang="zh",
            )
        )
        path = self.write_snapshot(snapshot)
        before = path.read_bytes()
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, path.read_bytes())

        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(
            names,
            ["grokbot_posts.json", "meta.json", "news.json", "posts_en.json", "posts_zh.json", "trends_ai.json"],
        )
        posts_zh = self.read_json(out / "posts_zh.json")
        self.assertEqual([p["text"] for p in posts_zh], ["更新的中文帖子", "中文帖子"])
        self.assertTrue(all(isinstance(p["id"], str) for p in posts_zh))
        self.assertEqual(posts_zh[0]["created_at"], "2026-10-25T02:00:00Z")

        meta = self.read_json(out / "meta.json")
        self.assertEqual(meta["schema_version"], 1)
        self.assertEqual(meta["generated_at"], snapshot["generated_at"])
        self.assertEqual(meta["summary"]["counts"]["posts_zh"], 2)
        self.assertEqual(meta["summary"]["counts"]["trends_ai"], 0)
        self.assertEqual(meta["snapshot_sha256"], sha256(before))
        for name, digest in meta["files_sha256"].items():
            self.assertEqual(digest, sha256((out / name).read_bytes()))
        self.assertEqual(self.read_json(out / "trends_ai.json"), [])

    def test_long_status_id_is_string(self) -> None:
        snapshot = base_snapshot()
        long_id = "123456789012345678901234567890"
        snapshot["posts_zh"][0]["url"] = f"https://x.com/zh_author/status/{long_id}"
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.read_json(out / "posts_zh.json")[0]
        self.assertEqual(record["id"], long_id)
        self.assertIsInstance(record["id"], str)

    def test_missing_count_is_null_and_zero_stays_zero(self) -> None:
        snapshot = base_snapshot()
        snapshot["posts_zh"] = [
            post(
                url="https://x.com/a/status/1000000000000000001",
                like_count=None,
                retweet_count=0,
            ),
            post(
                url="https://x.com/a/status/1000000000000000002",
                like_count="42",
                retweet_count="0",
            ),
        ]
        # Remove the keys entirely for the first record to test missing, not null.
        del snapshot["posts_zh"][0]["like_count"]
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = {item["id"]: item for item in self.read_json(out / "posts_zh.json")}
        missing = records["1000000000000000001"]
        numeric = records["1000000000000000002"]
        self.assertIsNone(missing["like_count"])
        self.assertEqual(missing["retweet_count"], 0)
        self.assertEqual(numeric["like_count"], 42)
        self.assertEqual(numeric["retweet_count"], 0)

    def test_sort_invalid_time_last_and_utc_normalization(self) -> None:
        snapshot = base_snapshot()
        snapshot["posts_zh"] = [
            post(
                text="invalid time",
                url="https://x.com/a/status/1000000000000000003",
                created_at="not-a-date",
            ),
            post(
                text="older",
                url="https://x.com/a/status/1000000000000000004",
                created_at="2026-10-20T08:00:00+08:00",
            ),
            post(
                text="newer",
                url="https://x.com/a/status/1000000000000000005",
                created_at="2026-10-24T08:00:00+08:00",
            ),
        ]
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.read_json(out / "posts_zh.json")
        self.assertEqual([item["text"] for item in records], ["newer", "older", "invalid time"])
        self.assertEqual(records[0]["created_at"], "2026-10-24T00:00:00Z")
        self.assertIsNone(records[2]["created_at"])
        self.assertIn("posts_zh[0]", result.stderr)

    def test_dedupe_keeps_first_and_fallback_id_is_stable(self) -> None:
        snapshot = base_snapshot()
        duplicate_url = "https://x.com/dupe/status/1000000000000000010"
        snapshot["posts_zh"] = [
            post(text="first kept", url=duplicate_url),
            post(text="second dropped", url=duplicate_url),
            post(text="no url but stable", url="javascript:alert(1)"),
        ]
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.read_json(out / "posts_zh.json")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["text"], "first kept")
        self.assertIn("duplicate id", result.stderr)

        fallback = next(item for item in records if item["text"] == "no url but stable")
        self.assertRegex(fallback["id"], r"^[0-9a-f]{64}$")
        self.assertIsNone(fallback["url"])

        out2 = self.tmp / "out2"
        second = self.run_fetch(path, out2)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            (out / "posts_zh.json").read_bytes(), (out2 / "posts_zh.json").read_bytes()
        )

    def test_empty_trends_and_all_empty_arrays_succeed(self) -> None:
        snapshot = base_snapshot()
        for field in ("posts_zh", "posts_en", "news", "trends_ai", "grokbot_posts"):
            snapshot[field] = []
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.read_json(out / "meta.json")
        self.assertEqual(set(meta["summary"]["counts"].values()), {0})
        self.assertEqual(self.read_json(out / "trends_ai.json"), [])
        self.assertEqual(self.read_json(out / "posts_zh.json"), [])

    def test_missing_root_field_fails(self) -> None:
        snapshot = base_snapshot()
        del snapshot["posts_en"]
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 1)
        self.assertIn("posts_en", result.stderr)
        self.assertFalse(out.exists())

    def test_null_array_bad_generated_at_and_missing_intro_fail(self) -> None:
        cases = []
        snapshot = base_snapshot()
        snapshot["trends_ai"] = None
        cases.append(("null array", snapshot, "trends_ai"))
        snapshot = base_snapshot()
        snapshot["generated_at"] = "2026-10-26T02:05:34"
        cases.append(("naive generated_at", snapshot, "generated_at"))
        snapshot = base_snapshot()
        del snapshot["grokbot_intro"]
        cases.append(("missing intro", snapshot, "grokbot_intro"))
        for label, snapshot, needle in cases:
            with self.subTest(label=label):
                path = self.write_snapshot(snapshot, name=f"{label.replace(' ', '_')}.json")
                out = self.tmp / f"out-{label.replace(' ', '_')}"
                result = self.run_fetch(path, out)
                self.assertEqual(result.returncode, 1)
                self.assertIn(needle, result.stderr)
                self.assertFalse(out.exists())

    def test_corrupt_json_does_not_overwrite_old_files(self) -> None:
        out = self.tmp / "out"
        out.mkdir()
        old = b'{"old": true}\n'
        for name in ("posts_zh.json", "posts_en.json", "news.json", "trends_ai.json", "grokbot_posts.json", "meta.json"):
            (out / name).write_bytes(old)
        path = self.tmp / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 1)
        for name in (
            "posts_zh.json",
            "posts_en.json",
            "news.json",
            "trends_ai.json",
            "grokbot_posts.json",
            "meta.json",
        ):
            self.assertEqual((out / name).read_bytes(), old)

    def test_check_writes_nothing(self) -> None:
        snapshot = base_snapshot()
        snapshot["extra_root"] = {"ignored": True}
        path = self.write_snapshot(snapshot)
        out = self.tmp / "never-created"
        result = self.run_fetch(path, out, check=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check ok", result.stdout)
        self.assertFalse(out.exists())

    def test_nonempty_group_with_no_valid_records_fails(self) -> None:
        snapshot = base_snapshot()
        snapshot["posts_zh"] = [{"author_name": "x"}]
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 1)
        self.assertIn("posts_zh", result.stderr)
        self.assertFalse(out.exists())

    def test_single_bad_record_warns_and_succeeds(self) -> None:
        snapshot = base_snapshot()
        snapshot["posts_zh"].insert(0, {"not": "a post"})
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("posts_zh[0]", result.stderr)
        self.assertIn("skipped", result.stderr)
        self.assertEqual(len(self.read_json(out / "posts_zh.json")), 1)

    def test_output_collision_with_input_is_rejected(self) -> None:
        snapshot = base_snapshot()
        out = self.tmp / "out"
        out.mkdir()
        path = out / "posts_zh.json"
        path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 1)
        self.assertIn("collides", result.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_fields_counted_and_ignored(self) -> None:
        snapshot = base_snapshot()
        snapshot["extra_root"] = {"ignored": True}
        snapshot["posts_zh"][0]["extra_post"] = "ignored"
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.read_json(out / "meta.json")
        self.assertGreaterEqual(meta["summary"]["ignored_fields"], 2)
        record = self.read_json(out / "posts_zh.json")[0]
        self.assertNotIn("extra_post", record)
        self.assertNotIn("extra_root", meta)

    def test_timezone_and_source_defaults(self) -> None:
        snapshot = base_snapshot()
        del snapshot["timezone"]
        del snapshot["source"]
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.read_json(out / "meta.json")
        self.assertEqual(meta["timezone"], "Asia/Shanghai")
        self.assertEqual(meta["source"], "本地快照")
        self.assertIn("timezone missing", result.stderr)
        self.assertIn("source missing", result.stderr)

    def test_url_safety_and_id_fallback(self) -> None:
        snapshot = base_snapshot()
        snapshot["posts_zh"] = [
            post(
                text="javascript url",
                url="javascript:alert(1)",
                created_at="2026-10-24T00:00:00Z",
            ),
            post(
                text="credential url",
                url="https://user:pass@x.com/u/status/1000000000000000020",
                created_at="2026-10-23T00:00:00Z",
            ),
            post(
                text="valid url",
                url="https://x.com/u/status/1000000000000000021",
                created_at="2026-10-22T00:00:00Z",
            ),
        ]
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.read_json(out / "posts_zh.json")
        by_text = {item["text"]: item for item in records}
        self.assertIsNone(by_text["javascript url"]["url"])
        self.assertIsNone(by_text["credential url"]["url"])
        self.assertEqual(
            by_text["valid url"]["url"], "https://x.com/u/status/1000000000000000021"
        )
        self.assertRegex(by_text["javascript url"]["id"], r"^[0-9a-f]{64}$")
        self.assertEqual(by_text["valid url"]["id"], "1000000000000000021")

    def test_repeat_normalization_is_byte_identical(self) -> None:
        snapshot = base_snapshot()
        path = self.write_snapshot(snapshot)
        out1 = self.tmp / "out1"
        out2 = self.tmp / "out2"
        first = self.run_fetch(path, out1)
        second = self.run_fetch(path, out2)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        for name in (
            "posts_zh.json",
            "posts_en.json",
            "news.json",
            "trends_ai.json",
            "grokbot_posts.json",
            "meta.json",
        ):
            with self.subTest(name=name):
                self.assertEqual((out1 / name).read_bytes(), (out2 / name).read_bytes())

    def test_news_fallback_id_and_missing_optional_fields(self) -> None:
        snapshot = base_snapshot()
        snapshot["news"] = [
            {
                "name": "No id or url",
                "summary": "",
                "updated_at": "2026-10-25T00:00:00Z",
                "topics": [],
            },
            {
                "id": "explicit-id",
                "name": "With id",
                "summary": None,
                "url": "javascript:alert(1)",
                "updated_at": "2026-10-26T00:00:00Z",
                "topics": ["AI"],
            },
        ]
        path = self.write_snapshot(snapshot)
        out = self.tmp / "out"
        result = self.run_fetch(path, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.read_json(out / "news.json")
        self.assertEqual(len(records), 2)
        fallback = next(item for item in records if item["name"] == "No id or url")
        self.assertRegex(fallback["id"], r"^[0-9a-f]{64}$")
        self.assertIsNone(fallback["url"])
        with_id = next(item for item in records if item["id"] == "explicit-id")
        self.assertEqual(with_id["summary"], "")
        self.assertIsNone(with_id["url"])

    def test_help_and_usage_errors(self) -> None:
        help_result = subprocess.run(
            [sys.executable, str(FETCH), "--help"], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--snapshot", help_result.stdout)
        bad = subprocess.run(
            [sys.executable, str(FETCH), "--does-not-exist"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
