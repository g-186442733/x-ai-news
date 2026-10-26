from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_site.py"
SHANGHAI = ZoneInfo("Asia/Shanghai")
GENERATED_AT = "2026-10-26T02:05:34+08:00"
NOW_FRESH = datetime(2026, 10, 26, 2, 30, tzinfo=SHANGHAI)
NOW_EXPIRED = datetime(2026, 10, 26, 5, 30, tzinfo=SHANGHAI)
NOW_FUTURE = datetime(2026, 10, 26, 1, 50, tzinfo=SHANGHAI)


def _load_build_module():
    spec = importlib.util.spec_from_file_location("build_site_under_test", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load_build_module()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_post(
    post_id: str = "1000000000000000001",
    *,
    text: str = "中文帖子唯一内容",
    author: str = "中文作者",
    username: str = "zh_author",
    created_at: str = "2026-10-24T10:00:00Z",
    likes=7,
    reposts=2,
    url: str | None = "https://x.com/zh_author/status/1000000000000000001",
    lang: str = "zh",
) -> dict:
    return {
        "id": post_id,
        "author_name": author,
        "author_username": username,
        "text": text,
        "created_at": created_at,
        "like_count": likes,
        "retweet_count": reposts,
        "url": url,
        "lang": lang,
    }


def make_news(
    record_id: str = "2114363022773002376",
    *,
    name: str = "AI 新闻标题",
    summary: str = "新闻摘要",
    url: str | None = "https://x.com/i/trending/2114363022773002376",
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


def default_intro() -> dict:
    return {
        "zh": {"title": "Grok Bot 是什么", "bullets": ["真实简介一", "真实简介二"]},
        "en": {"title": "What is Grok Bot", "bullets": ["Real intro one", "Real intro two"]},
    }


def write_dataset(
    root: Path,
    *,
    posts_zh=None,
    posts_en=None,
    news_items=None,
    trends=None,
    grokbot_posts=None,
    intro=None,
    fallback=None,
    trends_note: str = "当前没有明确的 AI 趋势。",
    generated_at: str = GENERATED_AT,
    schema_version: int = 1,
) -> Path:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    arrays = {
        "posts_zh": [make_post()] if posts_zh is None else posts_zh,
        "posts_en": [
            make_post(
                "2000000000000000001",
                text="English post unique content",
                author="English Author",
                username="en_author",
                url="https://x.com/en_author/status/2000000000000000001",
                lang="en",
            )
        ]
        if posts_en is None
        else posts_en,
        "news": [make_news()] if news_items is None else news_items,
        "trends_ai": [] if trends is None else trends,
        "grokbot_posts": [
            make_post(
                "3000000000000000001",
                text="Grok Bot 相关帖子唯一内容",
                author="Grok Fan",
                username="grokfan",
                url="https://x.com/grokfan/status/3000000000000000001",
                lang="en",
            )
        ]
        if grokbot_posts is None
        else grokbot_posts,
    }
    files_sha256 = {}
    for field, records in arrays.items():
        data = (json.dumps(records, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        name = f"{field}.json"
        (data_dir / name).write_bytes(data)
        files_sha256[name] = sha256(data)

    meta = {
        "schema_version": schema_version,
        "generated_at": generated_at,
        "timezone": "Asia/Shanghai",
        "source": "local test source",
        "trends_note": trends_note,
        "grokbot_intro": default_intro() if intro is None else intro,
        "grokbot_intro_fallback": {"zh": False, "en": False} if fallback is None else fallback,
        "summary": {
            "counts": {field: len(arrays[field]) for field in arrays},
            "warnings": 0,
            "ignored_fields": 0,
        },
        "snapshot_sha256": "b" * 64,
        "files_sha256": files_sha256,
    }
    (data_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return data_dir


class SiteInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.tabs: dict[str, str] = {}
        self.panels: dict[str, str] = {}
        self.selected: dict[str, str] = {}
        self.tabindex: dict[str, str] = {}
        self.scripts: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attributes = {key: (value if value is not None else "") for key, value in attrs}
        if "id" in attributes:
            self.ids.append(attributes["id"])
        role = attributes.get("role")
        if role == "tab":
            self.tabs[attributes.get("id", "")] = attributes.get("aria-controls", "")
            self.selected[attributes.get("id", "")] = attributes.get("aria-selected", "")
            self.tabindex[attributes.get("id", "")] = attributes.get("tabindex", "")
        elif role == "tabpanel":
            self.panels[attributes.get("id", "")] = attributes.get("aria-labelledby", "")
        if tag == "script":
            self.scripts.append(attributes)
        if tag == "link":
            self.links.append(attributes)


class BuildSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.data_dir = write_dataset(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self, *, out_name: str = "site", base_path: str = "/x-ai-news/", hours: float = 2, now=None):
        out = self.tmp / out_name
        result = build.build_site(
            data_dir=self.data_dir,
            out_dir=out,
            base_path=base_path,
            stale_after_hours=hours,
            now=NOW_FRESH if now is None else now,
        )
        return result, out / "index.html"

    def test_builds_exactly_three_files_and_copies_assets(self) -> None:
        result, index = self.build()
        out = result.out_dir
        files = sorted(path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file())
        self.assertEqual(files, ["assets/site.css", "assets/site.js", "index.html"])
        self.assertEqual((out / "assets/site.css").read_bytes(), (ROOT / "assets/site.css").read_bytes())
        self.assertEqual((out / "assets/site.js").read_bytes(), (ROOT / "assets/site.js").read_bytes())
        self.assertEqual(result.stale_status, "fresh")
        self.assertEqual(result.counts["posts_zh"], 1)
        self.assertEqual(result.counts["trends_ai"], 0)
        html = index.read_text(encoding="utf-8")
        self.assertIn("<!doctype html>", html)
        self.assertIn('<html lang="zh-Hans">', html)
        self.assertIn('name="viewport"', html)
        self.assertIn('href="./assets/site.css"', html)
        self.assertIn('src="./assets/site.js"', html)

    def test_grokbot_is_prominent_and_uses_real_intro(self) -> None:
        _, index = self.build()
        html = index.read_text(encoding="utf-8")
        self.assertIn('class="grokbot-hero"', html)
        self.assertIn("Grok Bot 特色专区", html)
        self.assertIn("Grok Bot Spotlight", html)
        self.assertIn("Grok Bot 是什么", html)
        self.assertIn("真实简介一", html)
        self.assertIn("Grok Bot 相关帖子唯一内容", html)
        self.assertLess(html.index("grokbot-hero"), html.index("posts-heading-zh"))
        self.assertLess(html.index("grokbot-hero"), html.index("posts-heading-en"))

    def test_both_languages_and_empty_states(self) -> None:
        _, index = self.build()
        html = index.read_text(encoding="utf-8")
        self.assertIn("中文帖子唯一内容", html)
        self.assertIn("English post unique content", html)
        self.assertIn('id="panel-zh"', html)
        self.assertIn('id="panel-en"', html)

        data_dir = write_dataset(
            self.tmp,
            posts_zh=[],
            posts_en=[],
            news_items=[],
            trends=[],
            trends_note="趋势说明原文",
        )
        out = self.tmp / "empty-site"
        build.build_site(data_dir=data_dir, out_dir=out, base_path="/", now=NOW_FRESH)
        empty_html = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("暂无中文帖子", empty_html)
        self.assertIn("No English posts yet", empty_html)
        self.assertIn("暂无新闻", empty_html)
        self.assertIn("No news yet", empty_html)
        self.assertIn("暂无", empty_html)
        self.assertIn("No AI trends at the moment", empty_html)
        self.assertIn("趋势说明原文", empty_html)

    def test_data_time_is_preserved_and_shanghai(self) -> None:
        _, index = self.build(now=NOW_FRESH)
        html = index.read_text(encoding="utf-8")
        self.assertIn("2026-10-26 02:05", html)
        self.assertIn("Asia/Shanghai", html)
        self.assertIn('data-source-generated-at="2026-10-26T02:05:34+08:00"', html)
        self.assertIn("数据更新 / Updated", html)
        self.assertNotIn("2026-10-26 02:30", html)
        meta = json.loads((self.data_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["generated_at"], GENERATED_AT)

    def test_fixed_clock_fresh_expired_future(self) -> None:
        fresh_result, fresh_index = self.build(out_name="fresh-site", now=NOW_FRESH)
        self.assertEqual(fresh_result.stale_status, "fresh")
        fresh_html = fresh_index.read_text(encoding="utf-8")
        self.assertRegex(fresh_html, r'id="stale-note"[^>]*\shidden')
        self.assertNotIn("数据可能已过期", fresh_html)
        self.assertNotIn("数据时间异常", fresh_html)

        expired_result, expired_index = self.build(out_name="expired-site", now=NOW_EXPIRED)
        self.assertEqual(expired_result.stale_status, "stale")
        expired_html = expired_index.read_text(encoding="utf-8")
        self.assertIn("数据可能已过期（超过 2 小时未更新）", expired_html)
        self.assertIn("Data may be stale (not updated for over 2 hours)", expired_html)

        future_result, future_index = self.build(out_name="future-site", now=NOW_FUTURE)
        self.assertEqual(future_result.stale_status, "future")
        future_html = future_index.read_text(encoding="utf-8")
        self.assertIn("数据时间异常（快照时间晚于当前时间）", future_html)

    def test_hash_mismatch_refuses_and_preserves_existing_site(self) -> None:
        result, index = self.build()
        before = index.read_bytes()
        (self.data_dir / "posts_zh.json").write_bytes(
            (self.data_dir / "posts_zh.json").read_bytes() + b"\n"
        )
        with self.assertRaises(build.DataError):
            build.build_site(
                data_dir=self.data_dir,
                out_dir=result.out_dir,
                base_path="/x-ai-news/",
                now=NOW_FRESH,
            )
        self.assertEqual(index.read_bytes(), before)

    def test_malicious_text_and_urls_are_inert(self) -> None:
        data_dir = write_dataset(
            self.tmp,
            posts_zh=[
                make_post(
                    text='</p><script>alert("x")</script><img src=x onerror=alert(1)>',
                    author='"><svg onload=alert(1)>',
                    url="javascript:alert(1)",
                )
            ],
            news_items=[
                make_news(name="<script>alert(2)</script>", url="javascript:alert(2)")
            ],
            grokbot_posts=[],
        )
        out = self.tmp / "xss-site"
        build.build_site(data_dir=data_dir, out_dir=out, base_path="/", now=NOW_FRESH)
        html = (out / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<svg onload", html)
        self.assertIn("&lt;script&gt;alert", html)
        self.assertIn("&lt;img src=x", html)
        inspector = SiteInspector()
        inspector.feed(html)
        self.assertEqual(len(inspector.scripts), 1)
        self.assertEqual(inspector.scripts[0].get("src"), "./assets/site.js")

    def test_dom_ids_unique_and_tabs_wired(self) -> None:
        _, index = self.build()
        html = index.read_text(encoding="utf-8")
        inspector = SiteInspector()
        inspector.feed(html)
        self.assertEqual(len(inspector.ids), len(set(inspector.ids)))
        self.assertEqual(set(inspector.tabs), {"tab-zh", "tab-en"})
        self.assertEqual(set(inspector.panels), {"panel-zh", "panel-en"})
        for tab_id, controls in inspector.tabs.items():
            self.assertIn(controls, inspector.panels)
        for panel_id, labelledby in inspector.panels.items():
            self.assertIn(labelledby, inspector.tabs)
        selected = [tab_id for tab_id, value in inspector.selected.items() if value == "true"]
        self.assertEqual(selected, ["tab-zh"])
        self.assertEqual(inspector.tabindex["tab-zh"], "0")
        self.assertEqual(inspector.tabindex["tab-en"], "-1")
        self.assertIn('role="tablist"', html)
        self.assertIn("<noscript>", html)
        self.assertIn('href="#panel-zh"', html)
        self.assertIn('href="#panel-en"', html)

    def test_root_and_project_base_paths(self) -> None:
        _, root_index = self.build(out_name="root-site", base_path="/")
        root_html = root_index.read_text(encoding="utf-8")
        self.assertIn('<link rel="canonical" href="/">', root_html)
        self.assertIn('<a class="brand-link" href="/">', root_html)
        self.assertIn('href="./assets/site.css"', root_html)
        self.assertNotIn('href="/assets/', root_html)

        _, project_index = self.build(out_name="project-site", base_path="x-ai-news")
        project_html = project_index.read_text(encoding="utf-8")
        self.assertIn('<link rel="canonical" href="/x-ai-news/">', project_html)
        self.assertIn('<a class="brand-link" href="/x-ai-news/">', project_html)
        self.assertIn('href="./assets/site.css"', project_html)
        self.assertNotIn('href="/assets/', project_html)

    def test_output_path_protection(self) -> None:
        protected = [
            ROOT.resolve(),
            ROOT.parent.resolve(),
            self.data_dir.resolve(),
            (ROOT / "scripts").resolve(),
            (ROOT / "assets").resolve(),
            (self.data_dir / "nested").resolve(),
        ]
        for out in protected:
            with self.subTest(out=out):
                with self.assertRaises(build.OutputError):
                    build._assert_output_path_safe(out, self.data_dir)
        # A normal dedicated directory is not rejected.
        build._assert_output_path_safe(self.tmp / "site", self.data_dir)

    def test_foreign_file_in_output_fails_without_removal(self) -> None:
        out = self.tmp / "site"
        out.mkdir()
        foreign = out / "notes.txt"
        foreign.write_text("keep me", encoding="utf-8")
        with self.assertRaises(build.OutputError):
            build.build_site(
                data_dir=self.data_dir,
                out_dir=out,
                base_path="/x-ai-news/",
                now=NOW_FRESH,
            )
        self.assertTrue(foreign.is_file())
        self.assertEqual(foreign.read_text(encoding="utf-8"), "keep me")

    def test_output_can_be_rebuilt(self) -> None:
        result, index = self.build()
        first = index.read_bytes()
        second_result, second_index = self.build()
        self.assertEqual(second_result.out_dir, result.out_dir)
        self.assertEqual(second_index.read_bytes(), first)
        files = sorted(
            path.relative_to(second_result.out_dir).as_posix()
            for path in second_result.out_dir.rglob("*")
            if path.is_file()
        )
        self.assertEqual(files, ["assets/site.css", "assets/site.js", "index.html"])

    def test_invalid_base_path_and_hours(self) -> None:
        invalid_paths = [
            "https://example.com/x/",
            "//evil.example/",
            "/a/../b/",
            "/x/?q=1",
            "/x/#frag",
            "/x\\y/",
            "",
        ]
        for value in invalid_paths:
            with self.subTest(base_path=value):
                with self.assertRaises(build.BuildError):
                    build.normalize_base_path(value)
        self.assertEqual(build.normalize_base_path("/"), "/")
        self.assertEqual(build.normalize_base_path("x-ai-news"), "/x-ai-news/")
        self.assertEqual(build.normalize_base_path("/x-ai-news/"), "/x-ai-news/")
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(hours=value):
                with self.assertRaises(build.BuildError):
                    build._validate_hours(value)

    def test_schema_version_mismatch_refuses(self) -> None:
        data_dir = write_dataset(self.tmp, schema_version=2)
        with self.assertRaises(build.DataError):
            build.build_site(
                data_dir=data_dir,
                out_dir=self.tmp / "schema-site",
                base_path="/",
                now=NOW_FRESH,
            )

    def test_nonempty_trends_and_zero_post_count_render(self) -> None:
        data_dir = write_dataset(
            self.tmp,
            trends=[
                {
                    "name": "AI chips",
                    "url": "https://x.com/i/trending/999",
                    "rank": 2,
                    "post_count": 0,
                }
            ],
        )
        out = self.tmp / "trend-site"
        build.build_site(data_dir=data_dir, out_dir=out, base_path="/", now=NOW_FRESH)
        html = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("#2", html)
        self.assertIn("AI chips", html)
        self.assertIn("0 帖子", html)
        self.assertNotIn("暂无</p>", html)

    def test_grokbot_intro_fallback_is_labelled(self) -> None:
        data_dir = write_dataset(
            self.tmp,
            intro={
                "zh": {"title": "What is Grok Bot", "bullets": ["English fallback"]},
                "en": {"title": "What is Grok Bot", "bullets": ["English fallback"]},
            },
            fallback={"zh": True, "en": False},
        )
        out = self.tmp / "fallback-site"
        build.build_site(data_dir=data_dir, out_dir=out, base_path="/", now=NOW_FRESH)
        html = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("以下简介为英文原文。", html)

    def test_cli_help_and_invalid_hours(self) -> None:
        help_result = subprocess.run(
            [sys.executable, str(BUILD_SCRIPT), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--data-dir", help_result.stdout)
        bad = subprocess.run(
            [sys.executable, str(BUILD_SCRIPT), "--stale-after-hours", "0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad.returncode, 2)

    def test_no_embedded_raw_json_or_inner_html_source(self) -> None:
        _, index = self.build()
        html = index.read_text(encoding="utf-8")
        js = (ROOT / "assets/site.js").read_text(encoding="utf-8")
        self.assertNotIn("application/json", html)
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("document.write", js)
        self.assertNotIn("fetch(", js)
        self.assertNotIn("XMLHttpRequest", js)


if __name__ == "__main__":
    unittest.main()
