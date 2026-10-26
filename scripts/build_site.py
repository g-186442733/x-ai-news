#!/usr/bin/env python3
"""Build the standalone static site from normalized snapshot data.

Only the seven normalized files are read.  No network access, X credentials, or
raw snapshot fallback is used.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1
LIST_FIELDS = ("posts_zh", "posts_en", "news", "industry_news", "trends_ai", "grokbot_posts")
ARRAY_FILES = {field: f"{field}.json" for field in LIST_FIELDS}
META_FILE = "meta.json"
OUTPUT_FILES = ("index.html", "assets/site.css", "assets/site.js")
OUTPUT_DIRS = {"assets"}

DEFAULT_BASE_PATH = "/x-ai-news/"
DEFAULT_STALE_AFTER_HOURS = 4.0
FUTURE_TOLERANCE = timedelta(minutes=5)
REPO_URL = "https://github.com/axureboutique/x-ai-news"
SITE_TITLE_ZH = "X AI 新闻站"
SITE_TITLE_EN = "X AI News"


class BuildError(Exception):
    """Base class for expected build failures."""


class DataError(BuildError):
    """The normalized dataset is missing or inconsistent."""


class OutputError(BuildError):
    """The requested output directory is unsafe or contains foreign files."""


try:
    SHANGHAI = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:  # pragma: no cover - fallback for minimal images
    SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


@dataclass
class BuildResult:
    out_dir: Path
    index_path: Path
    generated_at: str
    counts: dict[str, int]
    stale_after_hours: float
    stale_status: str
    warnings: int


def esc(value: Any) -> str:
    """Escape text and attribute values exactly once."""

    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _shanghai_text(value: datetime) -> str:
    return value.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")


def _hours_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def safe_url(value: Any) -> str | None:
    """Defense-in-depth URL validation for rendering links."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    if any(ch.isspace() for ch in text):
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return text


def normalize_base_path(value: str) -> str:
    if not isinstance(value, str):
        raise BuildError("base path must be a string")
    text = value.strip()
    if not text:
        raise BuildError("base path must not be empty")
    if "://" in text or text.startswith("//"):
        raise BuildError("base path must be a site-relative path, not a URL")
    if any(ch in text for ch in ("?", "#", "\\")):
        raise BuildError("base path must not contain a query, fragment, or backslash")
    if any(ch.isspace() or ord(ch) < 0x20 for ch in text):
        raise BuildError("base path must not contain whitespace or control characters")
    parts = [part for part in text.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise BuildError("base path must not contain dot segments")
    return "/" + "/".join(parts) + "/" if parts else "/"


def _validate_hours(value: Any) -> float:
    if isinstance(value, bool):
        raise BuildError("stale-after-hours must be a positive number")
    try:
        hours = float(value)
    except (TypeError, ValueError) as exc:
        raise BuildError("stale-after-hours must be a positive number") from exc
    if not math.isfinite(hours) or hours <= 0:
        raise BuildError("stale-after-hours must be a positive number")
    return hours


def _load_json(path: Path) -> tuple[Any, bytes]:
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise DataError(f"missing required file: {path}") from exc
    except OSError as exc:
        raise DataError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        return json.loads(data.decode("utf-8")), data
    except UnicodeDecodeError as exc:
        raise DataError(f"{path} is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise DataError(f"{path} is not valid JSON (line {exc.lineno})") from exc


def _require_string(container: dict[str, Any], key: str, where: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{where}.{key} must be a non-empty string")
    return value


def _validate_meta(meta: Any, arrays: dict[str, list[Any]], raw_bytes: dict[str, bytes]) -> None:
    if not isinstance(meta, dict):
        raise DataError("meta.json root must be an object")
    version = meta.get("schema_version")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise DataError(f"unsupported schema_version: {version!r}; expected {SCHEMA_VERSION}")
    generated = _parse_iso(meta.get("generated_at"))
    if generated is None:
        raise DataError("meta.json generated_at must be an ISO 8601 timestamp with timezone")
    _require_string(meta, "timezone", "meta.json")
    _require_string(meta, "source", "meta.json")
    if not isinstance(meta.get("trends_note"), str):
        raise DataError("meta.json trends_note must be a string")
    intro = meta.get("grokbot_intro")
    if not isinstance(intro, dict):
        raise DataError("meta.json grokbot_intro must be an object")
    for lang in ("zh", "en"):
        block = intro.get(lang)
        if not isinstance(block, dict):
            raise DataError(f"meta.json grokbot_intro.{lang} must be an object")
        if not isinstance(block.get("title", ""), str):
            raise DataError(f"meta.json grokbot_intro.{lang}.title must be a string")
        bullets = block.get("bullets", [])
        if not isinstance(bullets, list) or not all(isinstance(item, str) for item in bullets):
            raise DataError(f"meta.json grokbot_intro.{lang}.bullets must be an array of strings")
    fallback = meta.get("grokbot_intro_fallback")
    if fallback is not None:
        if not isinstance(fallback, dict):
            raise DataError("meta.json grokbot_intro_fallback must be an object")
        for lang in ("zh", "en"):
            if not isinstance(fallback.get(lang, False), bool):
                raise DataError(f"meta.json grokbot_intro_fallback.{lang} must be a boolean")

    summary = meta.get("summary")
    if summary is not None:
        if not isinstance(summary, dict):
            raise DataError("meta.json summary must be an object")
        counts = summary.get("counts")
        if counts is not None:
            if not isinstance(counts, dict):
                raise DataError("meta.json summary.counts must be an object")
            for field in LIST_FIELDS:
                value = counts.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise DataError(
                        f"meta.json summary.counts.{field} must be a non-negative integer"
                    )
                if value != len(arrays[field]):
                    raise DataError(
                        f"meta.json count mismatch for {field}: summary={value}, actual={len(arrays[field])}"
                    )
        warning_count = summary.get("warnings", 0)
        if (
            isinstance(warning_count, bool)
            or not isinstance(warning_count, int)
            or warning_count < 0
        ):
            raise DataError("meta.json summary.warnings must be a non-negative integer")

    files_sha256 = meta.get("files_sha256")
    if not isinstance(files_sha256, dict):
        raise DataError("meta.json files_sha256 must be an object")
    expected = set(ARRAY_FILES.values())
    if set(files_sha256) != expected:
        raise DataError("meta.json files_sha256 must describe exactly the five array files")
    for name in expected:
        digest = files_sha256.get(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DataError(f"meta.json files_sha256.{name} must be a SHA-256 hex digest")
        if digest != _sha256(raw_bytes[name]):
            raise DataError(f"digest mismatch for {name}; refusing a mixed data batch")


def _validate_posts(records: Any, field: str) -> None:
    if not isinstance(records, list):
        raise DataError(f"{field} must be an array")
    seen: set[str] = set()
    for index, item in enumerate(records):
        where = f"{ARRAY_FILES[field]} [{index}]"
        if not isinstance(item, dict):
            raise DataError(f"{where} must be an object")
        record_id = item.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise DataError(f"{where}.id must be a non-empty string")
        if record_id in seen:
            raise DataError(f"{field} contains duplicate id {record_id}")
        seen.add(record_id)
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            raise DataError(f"{where}.text must be a non-empty string")
        if not isinstance(item.get("author_name"), str) or not item["author_name"].strip():
            raise DataError(f"{where}.author_name must be a non-empty string")
        for key in ("author_username", "created_at", "url", "lang"):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise DataError(f"{where}.{key} must be a string or null")
        for key in ("like_count", "retweet_count"):
            value = item.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise DataError(f"{where}.{key} must be a non-negative integer or null")


def _validate_news_like(records: Any, field: str, *, require_source: bool = False) -> None:
    if not isinstance(records, list):
        raise DataError(f"{field} must be an array")
    seen: set[str] = set()
    for index, item in enumerate(records):
        where = f"{field}.json [{index}]"
        if not isinstance(item, dict):
            raise DataError(f"{where} must be an object")
        record_id = item.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise DataError(f"{where}.id must be a non-empty string")
        if record_id in seen:
            raise DataError(f"{field} contains duplicate id {record_id}")
        seen.add(record_id)
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise DataError(f"{where}.name must be a non-empty string")
        for key in ("summary", "url", "updated_at"):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise DataError(f"{where}.{key} must be a string or null")
        if require_source:
            source = item.get("source")
            if not isinstance(source, str) or not source.strip():
                raise DataError(f"{where}.source must be a non-empty string")
        topics = item.get("topics")
        if not isinstance(topics, list) or not all(isinstance(topic, str) for topic in topics):
            raise DataError(f"{where}.topics must be an array of strings")


def _validate_news(records: Any) -> None:
    _validate_news_like(records, "news", require_source=False)


def _validate_industry_news(records: Any) -> None:
    _validate_news_like(records, "industry_news", require_source=True)


def _validate_trends(records: Any) -> None:
    if not isinstance(records, list):
        raise DataError("trends_ai must be an array")
    for index, item in enumerate(records):
        where = f"trends_ai.json [{index}]"
        if not isinstance(item, dict):
            raise DataError(f"{where} must be an object")
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise DataError(f"{where}.name must be a non-empty string")
        if item.get("url") is not None and not isinstance(item["url"], str):
            raise DataError(f"{where}.url must be a string or null")
        rank = item.get("rank")
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
            raise DataError(f"{where}.rank must be a positive integer or null")
        count = item.get("post_count")
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < 0
        ):
            raise DataError(f"{where}.post_count must be a non-negative integer or null")


def load_dataset(data_dir: Path) -> tuple[dict[str, Any], dict[str, list[Any]], int]:
    if not data_dir.exists():
        raise DataError(f"data directory not found: {data_dir}")
    if not data_dir.is_dir():
        raise DataError(f"data path is not a directory: {data_dir}")

    raw_bytes: dict[str, bytes] = {}
    parsed: dict[str, Any] = {}
    for field in LIST_FIELDS:
        name = ARRAY_FILES[field]
        value, data = _load_json(data_dir / name)
        if not isinstance(value, list):
            raise DataError(f"{name} root must be an array")
        raw_bytes[name] = data
        parsed[field] = value
    meta, _ = _load_json(data_dir / META_FILE)

    _validate_posts(parsed["posts_zh"], "posts_zh")
    _validate_posts(parsed["posts_en"], "posts_en")
    _validate_posts(parsed["grokbot_posts"], "grokbot_posts")
    _validate_news(parsed["news"])
    _validate_industry_news(parsed["industry_news"])
    _validate_trends(parsed["trends_ai"])
    _validate_meta(meta, parsed, raw_bytes)

    warnings = meta.get("summary", {}).get("warnings", 0) if isinstance(meta.get("summary"), dict) else 0
    if isinstance(warnings, bool) or not isinstance(warnings, int) or warnings < 0:
        warnings = 0
    return meta, parsed, warnings


def _stale_status(generated: datetime, now: datetime, hours: float) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise BuildError("build clock must be timezone-aware")
    if generated - now > FUTURE_TOLERANCE:
        return "future"
    if now - generated > timedelta(hours=hours):
        return "stale"
    return "fresh"


def _stale_text(status: str, lang: str, hours: float) -> str:
    if status == "fresh":
        return ""
    amount = _hours_text(hours)
    if status == "future":
        return (
            "数据时间异常（快照时间晚于当前时间）"
            if lang == "zh"
            else "Data timestamp anomaly (snapshot time is in the future)"
        )
    return (
        f"数据可能已过期（超过 {amount} 小时未更新）"
        if lang == "zh"
        else f"Data may be stale (not updated for over {amount} hours)"
    )


def _render_time(value: Any, lang: str, *, generated: bool = False) -> str:
    parsed = _parse_iso(value)
    if parsed is None:
        return f'<span class="time-unknown">{esc("时间未知" if lang == "zh" else "Time unknown")}</span>'
    display = _shanghai_text(parsed)
    label = (
        f"{display}（Asia/Shanghai）"
        if lang == "zh"
        else f"{display} (Asia/Shanghai)"
    )
    source_attr = f' data-source-generated-at="{esc(value)}"' if generated else ""
    return (
        f'<time datetime="{esc(_utc_iso(parsed))}"{source_attr}>{esc(label)}</time>'
    )


def _render_external_link(url: Any, label: str, css_class: str = "") -> str:
    safe = safe_url(url)
    if not safe:
        return ""
    class_attr = f' class="{esc(css_class)}"' if css_class else ""
    return (
        f'<a{class_attr} href="{esc(safe)}" target="_blank" '
        f'rel="noopener noreferrer">{esc(label)}</a>'
    )


def _render_metric(label: str, value: Any) -> str:
    display = "—" if value is None else str(value)
    return (
        f'<span class="metric"><span class="metric-label">{esc(label)}</span> '
        f'<span class="metric-value">{esc(display)}</span></span>'
    )


def _render_post(post: dict[str, Any], lang: str, dom_id: str) -> str:
    zh = lang == "zh"
    author = post.get("author_name") or ("未知作者" if zh else "Unknown author")
    username = post.get("author_username")
    username_html = (
        f'<span class="username">@{esc(username)}</span>' if username else ""
    )
    post_lang = post.get("lang")
    lang_attr = ""
    if post_lang in {"zh", "en"}:
        lang_attr = f' lang="{post_lang}"'
    like = _render_metric("喜欢" if zh else "Likes", post.get("like_count"))
    repost = _render_metric("转发" if zh else "Reposts", post.get("retweet_count"))
    original = _render_external_link(
        post.get("url"),
        "查看原帖" if zh else "View original",
        "post-link",
    )
    original_html = f'<span class="post-link-wrap">{original}</span>' if original else ""
    return f"""
<article class="post-card" id="{esc(dom_id)}"{lang_attr}>
  <header class="post-head">
    <div class="post-author">
      <span class="author-name">{esc(author)}</span>
      {username_html}
    </div>
    <div class="post-time">{_render_time(post.get("created_at"), lang)}</div>
  </header>
  <p class="post-text">{esc(post.get("text", ""))}</p>
  <footer class="post-foot">
    <div class="post-metrics">{like}{repost}</div>
    {original_html}
  </footer>
</article>"""


def _render_news(item: dict[str, Any], lang: str, dom_id: str, *, show_source: bool = False) -> str:
    zh = lang == "zh"
    name = item.get("name", "")
    link = _render_external_link(item.get("url"), str(name), "news-link")
    title = link if link else esc(name)
    summary = item.get("summary")
    summary_html = f'<p class="news-summary">{esc(summary)}</p>' if summary else ""
    source_html = ""
    if show_source:
        source = item.get("source")
        if isinstance(source, str) and source.strip():
            label = "来源" if zh else "Source"
            source_html = (
                f'<p class="news-source"><span class="news-source-label">{esc(label)}</span>'
                f'{esc(source.strip())}</p>'
            )
    topics = item.get("topics") or []
    topics_html = ""
    if topics:
        chips = "".join(f'<li class="topic">{esc(topic)}</li>' for topic in topics)
        topics_html = f'<ul class="topics" aria-label="{esc("主题" if zh else "Topics")}">{chips}</ul>'
    return f"""
<article class="card news-card" id="{esc(dom_id)}">
  <div class="news-meta">{_render_time(item.get("updated_at"), lang)}</div>
  <h4 class="news-title">{title}</h4>
  {summary_html}
  {source_html}
  {topics_html}
</article>"""


def _render_trends(meta: dict[str, Any], trends: list[dict[str, Any]], lang: str) -> str:
    zh = lang == "zh"
    heading = "AI 趋势" if zh else "AI Trends"
    heading_id = f"trends-heading-{lang}"
    if not trends:
        empty = "暂无" if zh else "No AI trends at the moment"
        note = meta.get("trends_note", "")
        note_html = f'<p class="trends-note">{esc(note)}</p>' if note else ""
        return f"""
<section class="card trends-card" aria-labelledby="{heading_id}">
  <h3 class="section-title" id="{heading_id}">{heading}</h3>
  <p class="empty-state trend-empty">{empty}</p>
  {note_html}
</section>"""

    rows: list[str] = []
    for index, item in enumerate(trends):
        rank = item.get("rank")
        rank_html = (
            f'<span class="trend-rank">#{esc(rank)}</span>' if rank is not None else ""
        )
        name = item.get("name", "")
        link = _render_external_link(item.get("url"), str(name), "trend-link")
        name_html = link if link else f'<span class="trend-name">{esc(name)}</span>'
        count = item.get("post_count")
        count_html = ""
        if count is not None:
            count_html = (
                f'<span class="trend-count">{esc(count)} 帖子</span>'
                if zh
                else f'<span class="trend-count">{esc(count)} posts</span>'
            )
        rows.append(
            f'<li class="trend-item" id="{esc(f"{lang}-trend-{index}")}">'
            f'{rank_html}<span class="trend-body">{name_html}{count_html}</span></li>'
        )
    return f"""
<section class="card trends-card" aria-labelledby="{heading_id}">
  <h3 class="section-title" id="{heading_id}">{heading}</h3>
  <ol class="trend-list">{"".join(rows)}</ol>
</section>"""


def _render_grokbot(meta: dict[str, Any], posts: list[dict[str, Any]], lang: str) -> str:
    zh = lang == "zh"
    intro = meta.get("grokbot_intro", {}).get(lang, {})
    fallback = meta.get("grokbot_intro_fallback", {}).get(lang, False)
    title = intro.get("title", "") if isinstance(intro, dict) else ""
    bullets = intro.get("bullets", []) if isinstance(intro, dict) else []
    heading_id = f"grokbot-heading-{lang}"
    posts_heading_id = f"grokbot-posts-heading-{lang}"

    if title or bullets:
        intro_title = f'<p class="grokbot-intro-title">{esc(title)}</p>' if title else ""
        bullets_html = ""
        if bullets:
            items = "".join(f"<li>{esc(bullet)}</li>" for bullet in bullets)
            bullets_html = f'<ul class="grokbot-bullets">{items}</ul>'
        fallback_html = ""
        if fallback:
            text = (
                "以下简介为英文原文。"
                if zh
                else "The introduction below is the Chinese original."
            )
            fallback_html = f'<p class="grokbot-fallback">{esc(text)}</p>'
        intro_html = f'{intro_title}{fallback_html}{bullets_html}'
    else:
        intro_html = f'<p class="empty-state">{esc("简介暂无" if zh else "Introduction unavailable")}</p>'

    if posts:
        cards = "".join(
            _render_post(post, lang, f"{lang}-grok-post-{index}")
            for index, post in enumerate(posts)
        )
        posts_html = f'<div class="grokbot-post-list">{cards}</div>'
    else:
        posts_html = (
            f'<p class="empty-state">{esc("暂无相关帖子" if zh else "No related posts yet")}</p>'
        )

    kicker = "FEATURED · 特色专区" if zh else "FEATURED · SPOTLIGHT"
    title_text = "Grok Bot 特色专区" if zh else "Grok Bot Spotlight"
    posts_title = "Grok Bot 相关帖子" if zh else "Grok Bot posts"
    return f"""
<section class="grokbot-hero" aria-labelledby="{heading_id}">
  <div class="grokbot-head">
    <p class="grokbot-kicker">{esc(kicker)}</p>
    <h3 class="grokbot-title" id="{heading_id}">{esc(title_text)}</h3>
    <div class="grokbot-intro">{intro_html}</div>
  </div>
  <div class="grokbot-feed">
    <h4 class="grokbot-posts-title" id="{posts_heading_id}">{esc(posts_title)}</h4>
    {posts_html}
  </div>
</section>"""


def _render_panel(meta: dict[str, Any], arrays: dict[str, list[Any]], lang: str) -> str:
    zh = lang == "zh"
    panel_lang = "zh-Hans" if zh else "en"
    posts = arrays["posts_zh"] if zh else arrays["posts_en"]
    posts_heading_id = f"posts-heading-{lang}"
    news_heading_id = f"news-heading-{lang}"
    industry_heading_id = f"industry-news-heading-{lang}"
    posts_heading = "AI 帖子" if zh else "AI Posts"
    news_heading = "AI 新闻" if zh else "AI News"
    industry_heading = "产业趋势与商业动态" if zh else "Industry & Business"
    empty_posts = "暂无中文帖子" if zh else "No English posts yet"
    empty_news = "暂无新闻" if zh else "No news yet"
    empty_industry = "暂无产业新闻" if zh else "No industry news yet"

    if posts:
        post_cards = "".join(
            _render_post(post, lang, f"{lang}-post-{index}")
            for index, post in enumerate(posts)
        )
        posts_html = f'<div class="post-list">{post_cards}</div>'
    else:
        posts_html = f'<p class="empty-state">{esc(empty_posts)}</p>'

    if arrays["news"]:
        news_cards = "".join(
            _render_news(item, lang, f"{lang}-news-{index}")
            for index, item in enumerate(arrays["news"])
        )
        news_html = f'<div class="news-list">{news_cards}</div>'
    else:
        news_html = f'<p class="empty-state">{esc(empty_news)}</p>'

    if arrays["industry_news"]:
        industry_cards = "".join(
            _render_news(
                item,
                lang,
                f"{lang}-industry-news-{index}",
                show_source=True,
            )
            for index, item in enumerate(arrays["industry_news"])
        )
        industry_html = f'<div class="news-list industry-news-list">{industry_cards}</div>'
    else:
        industry_html = f'<p class="empty-state">{esc(empty_industry)}</p>'

    trends_html = _render_trends(meta, arrays["trends_ai"], lang)
    grok_html = _render_grokbot(meta, arrays["grokbot_posts"], lang)

    return f"""
<section class="lang-panel" id="panel-{lang}" role="tabpanel" aria-labelledby="tab-{lang}" tabindex="0" lang="{panel_lang}" data-panel-lang="{lang}">
  <div class="panel-heading-row">
    <h2 class="panel-heading" id="panel-heading-{lang}">{"中文" if zh else "English"}</h2>
    <p class="panel-subheading">{"中文帖子与界面" if zh else "English posts and interface"}</p>
  </div>
  {grok_html}
  <div class="content-grid">
    <section class="feed-column" aria-labelledby="{posts_heading_id}">
      <h3 class="section-title" id="{posts_heading_id}">{esc(posts_heading)}</h3>
      {posts_html}
    </section>
    <aside class="sidebar" aria-label="{esc("新闻、产业与趋势" if zh else "News, industry, and trends")}">
      <section class="card news-section" aria-labelledby="{news_heading_id}">
        <h3 class="section-title" id="{news_heading_id}">{esc(news_heading)}</h3>
        {news_html}
      </section>
      <section class="card news-section industry-news-section" aria-labelledby="{industry_heading_id}">
        <h3 class="section-title" id="{industry_heading_id}">{esc(industry_heading)}</h3>
        {industry_html}
      </section>
      {trends_html}
    </aside>
  </div>
</section>"""


def render_page(
    meta: dict[str, Any],
    arrays: dict[str, list[Any]],
    base_path: str,
    stale_after_hours: float,
    now: datetime,
) -> str:
    generated = _parse_iso(meta.get("generated_at"))
    if generated is None:
        raise DataError("meta.json generated_at must be an ISO 8601 timestamp with timezone")
    status = _stale_status(generated, now, stale_after_hours)
    hours_attr = _hours_text(stale_after_hours)
    stale_zh = _stale_text(status, "zh", stale_after_hours)
    stale_en = _stale_text(status, "en", stale_after_hours)
    hidden = " hidden" if status == "fresh" else ""
    description = "X 公开信息快照：Grok Bot 特色、AI 帖子、AI 新闻、产业趋势与商业动态、AI 趋势。"
    tabs = f"""
      <div class="lang-tabs" role="tablist" aria-label="语言 / Language">
        <button class="lang-tab" type="button" role="tab" id="tab-zh" aria-selected="true" aria-controls="panel-zh" tabindex="0" data-lang="zh" lang="zh-Hans">中文</button>
        <button class="lang-tab" type="button" role="tab" id="tab-en" aria-selected="false" aria-controls="panel-en" tabindex="-1" data-lang="en" lang="en">English</button>
      </div>
      <noscript>
        <p class="nojs-nav">无 JavaScript：两种语言内容同时显示。 <a href="#panel-zh">中文</a> · <a href="#panel-en">English</a></p>
        <p class="nojs-nav" lang="en">JavaScript is off: both languages are shown. <a href="#panel-zh">Chinese</a> · <a href="#panel-en">English</a></p>
      </noscript>"""

    zh_panel = _render_panel(meta, arrays, "zh")
    en_panel = _render_panel(meta, arrays, "en")
    generated_time = _render_time(meta["generated_at"], "zh", generated=True)
    source = meta.get("source", "")

    return f"""<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{esc(description)}">
  <meta name="theme-color" content="#0b1220">
  <title>{esc(SITE_TITLE_ZH)} · {esc(SITE_TITLE_EN)}</title>
  <link rel="canonical" href="{esc(base_path)}">
  <link rel="stylesheet" href="./assets/site.css">
  <script src="./assets/site.js" defer></script>
</head>
<body data-generated-at="{esc(_utc_iso(generated))}" data-source-generated-at="{esc(meta["generated_at"])}" data-stale-after-hours="{esc(hours_attr)}" data-title-zh="{esc(SITE_TITLE_ZH)}" data-title-en="{esc(SITE_TITLE_EN)}">
  <a class="skip-link" href="#main">跳到主内容 / Skip to content</a>
  <header class="site-header">
    <div class="wrap header-grid">
      <div class="brand">
        <p class="eyebrow">X AI NEWS · PUBLIC SNAPSHOT</p>
        <h1 class="site-title"><a class="brand-link" href="{esc(base_path)}">{esc(SITE_TITLE_ZH)} <span class="brand-en">{esc(SITE_TITLE_EN)}</span></a></h1>
        <p class="update-line"><span class="update-label">数据更新 / Updated</span> {generated_time}</p>
        <p id="stale-note" class="stale-note" role="status" aria-live="polite"{hidden}><span data-stale-text-zh>{esc(stale_zh)}</span><span class="stale-sep" aria-hidden="true"> · </span><span data-stale-text-en>{esc(stale_en)}</span></p>
      </div>
      <div class="language-switch">
        {tabs}
      </div>
    </div>
  </header>
  <main id="main" class="wrap site-main">
    {zh_panel}
    {en_panel}
  </main>
  <footer class="site-footer">
    <div class="wrap footer-inner">
      <p>内容来自 X 公开信息快照，原文与互动数以采集时为准。</p>
      <p lang="en">Content comes from a public X snapshot; original text and engagement counts are as captured.</p>
      <p class="footer-meta">快照 / Snapshot: {_render_time(meta["generated_at"], "zh")} · <a href="{esc(REPO_URL)}" target="_blank" rel="noopener noreferrer">GitHub 仓库 / Repository</a></p>
      <details class="source-details">
        <summary>来源与构建说明 / Source &amp; build notes</summary>
        <p>来源 / Source: {esc(source)}</p>
        <p>本页为离线生成的静态页面，浏览时不会请求 X 或其它远程数据接口。</p>
        <p lang="en">This is an offline static page; browsing it does not query X or other remote data APIs.</p>
      </details>
    </div>
  </footer>
</body>
</html>
"""


def _assert_output_path_safe(out_dir: Path, data_dir: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    out = out_dir.resolve()
    data = data_dir.resolve()

    protected_exact = [
        repo_root,
        data,
        repo_root / "scripts",
        repo_root / "assets",
        repo_root / "tests",
        repo_root / "docs",
        repo_root / ".github",
    ]
    for protected in protected_exact:
        protected = protected.resolve()
        if out == protected:
            raise OutputError(f"output directory must not be {protected}")
        if protected.is_relative_to(out):
            raise OutputError(f"output directory must not be an ancestor of {protected}")

    for protected in (
        repo_root / "scripts",
        repo_root / "assets",
        repo_root / "tests",
        repo_root / "docs",
        repo_root / ".github",
    ):
        if out.is_relative_to(protected.resolve()):
            raise OutputError(f"output directory must not be inside {protected}")
    if out.is_relative_to(data):
        raise OutputError(f"output directory must not be inside the data directory {data}")


def _assert_no_foreign_files(out_dir: Path) -> None:
    if not out_dir.exists():
        return
    if out_dir.is_symlink():
        raise OutputError("output directory must not be a symlink")
    if not out_dir.is_dir():
        raise OutputError(f"output path exists and is not a directory: {out_dir}")

    allowed_files = set(OUTPUT_FILES)
    for root, dirs, files in os.walk(out_dir, followlinks=False):
        root_path = Path(root)
        for name in dirs:
            path = root_path / name
            rel = path.relative_to(out_dir).as_posix()
            if path.is_symlink():
                raise OutputError(f"output directory contains a symlink: {rel}")
            if rel not in OUTPUT_DIRS:
                raise OutputError(f"output directory contains an unexpected directory: {rel}")
        for name in files:
            path = root_path / name
            rel = path.relative_to(out_dir).as_posix()
            if path.is_symlink():
                raise OutputError(f"output directory contains a symlink: {rel}")
            if rel not in allowed_files:
                raise OutputError(f"output directory contains a foreign file: {rel}")

    for rel in OUTPUT_FILES:
        path = out_dir / rel
        if path.exists() and not path.is_file():
            raise OutputError(f"expected output file is not a regular file: {rel}")


def _assert_exact_output(directory: Path) -> None:
    found: set[str] = set()
    for root, dirs, files in os.walk(directory):
        for name in files:
            found.add((Path(root) / name).relative_to(directory).as_posix())
    if found != set(OUTPUT_FILES):
        raise OutputError(
            "temporary build did not produce exactly the required files: "
            + ", ".join(sorted(found))
        )
    for rel in OUTPUT_FILES:
        if not (directory / rel).is_file() or (directory / rel).stat().st_size == 0:
            raise OutputError(f"generated file is empty or missing: {rel}")


def _install_output(temp_dir: Path, out_dir: Path) -> None:
    backup: Path | None = None
    if out_dir.exists():
        backup = out_dir.parent / f".{out_dir.name}.backup-{uuid.uuid4().hex}"
        os.replace(out_dir, backup)
        try:
            os.replace(temp_dir, out_dir)
        except BaseException:
            if backup.exists() and not out_dir.exists():
                os.replace(backup, out_dir)
            raise
        else:
            shutil.rmtree(backup, ignore_errors=True)
    else:
        os.replace(temp_dir, out_dir)


def build_site(
    data_dir: Path | str = "data",
    out_dir: Path | str = "site",
    base_path: str = DEFAULT_BASE_PATH,
    stale_after_hours: float = DEFAULT_STALE_AFTER_HOURS,
    now: datetime | None = None,
) -> BuildResult:
    data_path = Path(data_dir).expanduser()
    out_path = Path(out_dir).expanduser()
    normalized_base = normalize_base_path(base_path)
    hours = _validate_hours(stale_after_hours)
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise BuildError("build clock must be timezone-aware")

    if out_path.is_symlink():
        raise OutputError("output directory must not be a symlink")
    out_resolved = out_path.resolve()
    _assert_output_path_safe(out_resolved, data_path)
    _assert_no_foreign_files(out_resolved)

    meta, arrays, warnings = load_dataset(data_path)
    generated = _parse_iso(meta.get("generated_at"))
    if generated is None:
        raise DataError("meta.json generated_at must be an ISO 8601 timestamp with timezone")
    status = _stale_status(generated, now, hours)

    page = render_page(meta, arrays, normalized_base, hours, now)

    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    css_source = assets_dir / "site.css"
    js_source = assets_dir / "site.js"
    for source in (css_source, js_source):
        if not source.is_file():
            raise BuildError(f"missing site asset: {source}")

    out_resolved.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{out_resolved.name}.build-", dir=out_resolved.parent)
    )
    try:
        temp_dir.chmod(0o755)
        (temp_dir / "assets").mkdir(mode=0o755)
        (temp_dir / "index.html").write_text(page, encoding="utf-8")
        shutil.copyfile(css_source, temp_dir / "assets" / "site.css")
        shutil.copyfile(js_source, temp_dir / "assets" / "site.js")
        _assert_exact_output(temp_dir)
        _assert_no_foreign_files(out_resolved)
        _install_output(temp_dir, out_resolved)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    counts = {field: len(arrays[field]) for field in LIST_FIELDS}
    return BuildResult(
        out_dir=out_resolved,
        index_path=out_resolved / "index.html",
        generated_at=meta["generated_at"],
        counts=counts,
        stale_after_hours=hours,
        stale_status=status,
        warnings=warnings,
    )


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the static site from normalized snapshot JSON. "
            "No network access and no X credentials."
        )
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        metavar="DIR",
        help="normalized data directory (default: data)",
    )
    parser.add_argument(
        "--out-dir",
        default="site",
        metavar="DIR",
        help="dedicated static output directory (default: site)",
    )
    parser.add_argument(
        "--base-path",
        default=DEFAULT_BASE_PATH,
        metavar="PATH",
        help=f"site base path (default: {DEFAULT_BASE_PATH})",
    )
    parser.add_argument(
        "--stale-after-hours",
        default=DEFAULT_STALE_AFTER_HOURS,
        type=_positive_float,
        metavar="N",
        help="hours before showing a stale-data notice (default: 4)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = build_site(
            data_dir=args.data_dir,
            out_dir=args.out_dir,
            base_path=args.base_path,
            stale_after_hours=args.stale_after_hours,
        )
    except (BuildError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # Defensive: do not dump page or dataset content.
        print(f"error: unexpected build failure ({type(exc).__name__})", file=sys.stderr)
        return 1

    counts = " ".join(f"{field}={result.counts[field]}" for field in LIST_FIELDS)
    print(f"built {result.index_path} ({counts})")
    print(
        f"snapshot_generated_at={result.generated_at} "
        f"stale_after_hours={_hours_text(result.stale_after_hours)} "
        f"stale_status={result.stale_status} warnings={result.warnings}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
