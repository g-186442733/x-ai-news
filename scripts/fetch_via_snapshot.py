#!/usr/bin/env python3
"""Normalize the locally captured X snapshot into stable, build-ready JSON.

This is a local-file adapter only.  It never opens a network connection, never
reads X credentials, and never modifies the source snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
LIST_FIELDS = ("posts_zh", "posts_en", "news", "trends_ai", "grokbot_posts")
ARRAY_FILES = {field: f"{field}.json" for field in LIST_FIELDS}
META_FILE = "meta.json"
OUTPUT_FILES = tuple(ARRAY_FILES[field] for field in LIST_FIELDS) + (META_FILE,)
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_SOURCE = "本地快照"
UNKNOWN_AUTHOR = "未知作者"

ROOT_KEYS = {
    "generated_at",
    "timezone",
    "source",
    "trends_note",
    "grokbot_intro",
    *LIST_FIELDS,
}
POST_KEYS = {
    "author_name",
    "author_username",
    "text",
    "created_at",
    "like_count",
    "retweet_count",
    "url",
    "lang",
}
NEWS_KEYS = {"id", "name", "summary", "url", "updated_at", "topics"}
TREND_KEYS = {"name", "url", "rank", "post_count"}
INTRO_KEYS = {"zh", "en"}
INTRO_LANG_KEYS = {"title", "bullets"}

X_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
STATUS_RE = re.compile(r"/status/([0-9]+)(?:/|$)")
DIGITS_RE = re.compile(r"^[0-9]+$")


class SnapshotError(Exception):
    """Base class for expected, reportable failures."""


class ContractError(SnapshotError):
    """The snapshot does not satisfy the normalization contract."""


class WarningLog:
    """Collect deterministic warnings without leaking record payloads."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self.ignored_fields = 0

    def add(self, where: str, reason: str) -> None:
        self.items.append(f"{where}: {reason}")

    def ignore_fields(self, raw: dict[str, Any], allowed: set[str], where: str) -> None:
        ignored = sorted(set(raw) - allowed)
        if ignored:
            self.ignored_fields += len(ignored)
            self.add(where, f"ignored {len(ignored)} unknown field(s): {', '.join(ignored)}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_id(*parts: str) -> str:
    return _sha256("\x1f".join(parts).encode("utf-8"))


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _parse_aware_datetime(value: Any, where: str, warnings: WarningLog | None = None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        if warnings is not None:
            warnings.add(where, "invalid ISO 8601 timestamp; value set to null")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if warnings is not None:
            warnings.add(where, "timestamp is missing a timezone; value set to null")
        return None
    return parsed


def _utc_text(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def safe_url(value: Any) -> str | None:
    """Return a normalized http(s) URL only when it is structurally safe."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
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
    if any(ch.isspace() for ch in text):
        return None
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not host or "." not in host and host != "localhost":
        return None
    return text


def _extract_status_id(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if host not in X_HOSTS:
        return None
    match = STATUS_RE.search(parsed.path)
    if not match:
        return None
    return match.group(1)


def _coerce_count(value: Any, *, positive: bool, where: str, warnings: WarningLog) -> int | None:
    minimum = 1 if positive else 0
    if isinstance(value, bool):
        warnings.add(where, "boolean is not a valid count; value set to null")
        return None
    if isinstance(value, int):
        if value >= minimum:
            return value
        warnings.add(where, f"count must be >= {minimum}; value set to null")
        return None
    if isinstance(value, str):
        text = value.strip()
        if DIGITS_RE.fullmatch(text):
            parsed = int(text)
            if parsed >= minimum:
                return parsed
        warnings.add(where, f"count must be a {'positive' if positive else 'non-negative'} integer; value set to null")
        return None
    if value is not None:
        warnings.add(where, f"count must be a {'positive' if positive else 'non-negative'} integer; value set to null")
    return None


def _raw_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _normalize_post(
    raw: Any,
    where: str,
    warnings: WarningLog,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        warnings.add(where, "record is not an object; skipped")
        return None
    warnings.ignore_fields(raw, POST_KEYS, where)

    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        warnings.add(where, "text must be a non-empty string; skipped")
        return None

    author_name = _optional_string(raw.get("author_name"))
    author_username = _optional_string(raw.get("author_username"))
    if not author_name:
        author_name = author_username or UNKNOWN_AUTHOR

    url = safe_url(raw.get("url"))
    if raw.get("url") is not None and url is None:
        warnings.add(where, "url is not a safe http(s) URL; value set to null")

    created_dt = _parse_aware_datetime(raw.get("created_at"), where, warnings)
    created_at = _utc_text(created_dt) if created_dt else None

    like_count = _coerce_count(
        raw.get("like_count"), positive=False, where=f"{where}.like_count", warnings=warnings
    )
    retweet_count = _coerce_count(
        raw.get("retweet_count"), positive=False, where=f"{where}.retweet_count", warnings=warnings
    )

    status_id = _extract_status_id(url)
    if status_id:
        record_id = status_id
    else:
        record_id = _stable_id(
            author_username or author_name or UNKNOWN_AUTHOR,
            text,
            created_at or "",
        )

    lang = _optional_string(raw.get("lang"))

    return {
        "id": str(record_id),
        "author_name": author_name,
        "author_username": author_username,
        "text": text,
        "created_at": created_at,
        "like_count": like_count,
        "retweet_count": retweet_count,
        "url": url,
        "lang": lang,
        # Internal sorting key; stripped before serialization.
        "_created_dt": created_dt,
    }


def _sort_posts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(record: dict[str, Any]) -> tuple[bool, float, str]:
        created_dt = record["_created_dt"]
        if created_dt is None:
            return (True, 0.0, record["id"])
        return (False, -created_dt.timestamp(), record["id"])

    records.sort(key=key)
    for record in records:
        record.pop("_created_dt", None)
    return records


def _dedupe(
    records: list[dict[str, Any]],
    where: str,
    warnings: WarningLog,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        if record["id"] in seen:
            warnings.add(where, f"duplicate id {record['id']}; kept first occurrence")
            continue
        seen.add(record["id"])
        result.append(record)
    return result


def _normalize_posts(
    raw_list: list[Any],
    field: str,
    warnings: WarningLog,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_list):
        where = f"{field}[{index}]"
        record = _normalize_post(raw, where, warnings)
        if record is not None:
            records.append(record)
    if raw_list and not records:
        raise ContractError(f"{field}: non-empty input produced no usable records")
    records = _dedupe(records, field, warnings)
    return _sort_posts(records)


def _normalize_news(
    raw_list: list[Any],
    warnings: WarningLog,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_list):
        where = f"news[{index}]"
        if not isinstance(raw, dict):
            warnings.add(where, "record is not an object; skipped")
            continue
        warnings.ignore_fields(raw, NEWS_KEYS, where)

        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.add(where, "name must be a non-empty string; skipped")
            continue

        summary = raw.get("summary")
        if summary is None:
            summary = ""
        elif not isinstance(summary, str):
            warnings.add(where, "summary must be a string; value set to empty")
            summary = ""

        topics_raw = raw.get("topics")
        if topics_raw is None:
            topics: list[str] = []
        elif isinstance(topics_raw, list):
            topics = []
            for topic in topics_raw:
                if isinstance(topic, str) and topic.strip():
                    topics.append(topic)
                else:
                    warnings.add(where, "non-string or empty topic ignored")
        else:
            warnings.add(where, "topics must be an array; value set to empty")
            topics = []

        url = safe_url(raw.get("url"))
        if raw.get("url") is not None and url is None:
            warnings.add(where, "url is not a safe http(s) URL; value set to null")

        updated_dt = _parse_aware_datetime(raw.get("updated_at"), where, warnings)
        updated_at = _utc_text(updated_dt) if updated_dt else None

        record_id = _raw_id(raw.get("id"))
        if not record_id:
            if url:
                record_id = _stable_id("url", url)
            else:
                record_id = _stable_id("text", name, summary)

        records.append(
            {
                "id": str(record_id),
                "name": name,
                "summary": summary,
                "url": url,
                "updated_at": updated_at,
                "topics": topics,
                "_updated_dt": updated_dt,
            }
        )

    if raw_list and not records:
        raise ContractError("news: non-empty input produced no usable records")
    records = _dedupe(records, "news", warnings)

    def key(record: dict[str, Any]) -> tuple[bool, float, str]:
        updated_dt = record["_updated_dt"]
        if updated_dt is None:
            return (True, 0.0, record["id"])
        return (False, -updated_dt.timestamp(), record["id"])

    records.sort(key=key)
    for record in records:
        record.pop("_updated_dt", None)
    return records


def _normalize_trends(
    raw_list: list[Any],
    warnings: WarningLog,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_list):
        where = f"trends_ai[{index}]"
        if not isinstance(raw, dict):
            warnings.add(where, "record is not an object; skipped")
            continue
        warnings.ignore_fields(raw, TREND_KEYS, where)

        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.add(where, "name must be a non-empty string; skipped")
            continue

        url = safe_url(raw.get("url"))
        if raw.get("url") is not None and url is None:
            warnings.add(where, "url is not a safe http(s) URL; value set to null")

        rank = _coerce_count(
            raw.get("rank"), positive=True, where=f"{where}.rank", warnings=warnings
        )
        post_count = _coerce_count(
            raw.get("post_count"),
            positive=False,
            where=f"{where}.post_count",
            warnings=warnings,
        )
        records.append(
            {
                "name": name,
                "url": url,
                "rank": rank,
                "post_count": post_count,
                "_index": index,
            }
        )

    if raw_list and not records:
        raise ContractError("trends_ai: non-empty input produced no usable records")

    records.sort(
        key=lambda record: (
            record["rank"] is None,
            record["rank"] if record["rank"] is not None else 0,
            record["_index"],
        )
    )
    for record in records:
        record.pop("_index", None)
    return records


def _normalize_intro(
    raw: dict[str, Any],
    warnings: WarningLog,
) -> tuple[dict[str, dict[str, Any]], dict[str, bool]]:
    warnings.ignore_fields(raw, INTRO_KEYS, "grokbot_intro")
    blocks: dict[str, dict[str, Any]] = {}
    for lang in ("zh", "en"):
        where = f"grokbot_intro.{lang}"
        block = raw.get(lang)
        if not isinstance(block, dict):
            warnings.add(where, "missing or non-object language block")
            blocks[lang] = {"title": "", "bullets": []}
            continue
        warnings.ignore_fields(block, INTRO_LANG_KEYS, where)
        title = block.get("title")
        if not isinstance(title, str):
            if title is not None:
                warnings.add(where, "title must be a string; value set to empty")
            title = ""
        bullets_raw = block.get("bullets")
        bullets: list[str] = []
        if bullets_raw is None:
            bullets = []
        elif isinstance(bullets_raw, list):
            for item in bullets_raw:
                if isinstance(item, str) and item.strip():
                    bullets.append(item)
                else:
                    warnings.add(where, "non-string or empty bullet ignored")
        else:
            warnings.add(where, "bullets must be an array; value set to empty")
        blocks[lang] = {"title": title.strip(), "bullets": bullets}

    def usable(block: dict[str, Any]) -> bool:
        return bool(block.get("title")) or bool(block.get("bullets"))

    fallback = {"zh": False, "en": False}
    if not usable(blocks["zh"]) and usable(blocks["en"]):
        blocks["zh"] = {
            "title": blocks["en"]["title"],
            "bullets": list(blocks["en"]["bullets"]),
        }
        fallback["zh"] = True
        warnings.add("grokbot_intro.zh", "no usable Chinese intro; using English original")
    elif not usable(blocks["en"]) and usable(blocks["zh"]):
        blocks["en"] = {
            "title": blocks["zh"]["title"],
            "bullets": list(blocks["zh"]["bullets"]),
        }
        fallback["en"] = True
        warnings.add("grokbot_intro.en", "no usable English intro; using Chinese original")
    return blocks, fallback


def normalize_snapshot(
    snapshot: dict[str, Any],
    snapshot_bytes: bytes,
) -> tuple[dict[str, bytes], dict[str, Any], list[str], int]:
    """Validate and normalize a parsed snapshot entirely in memory.

    Returns ``(outputs, meta, warnings, ignored_field_count)`` where outputs maps
    the six file names to their final UTF-8 bytes.
    """

    if not isinstance(snapshot, dict):
        raise ContractError("snapshot root must be a JSON object")

    warnings = WarningLog()
    warnings.ignore_fields(snapshot, ROOT_KEYS, "root")

    for field in LIST_FIELDS:
        if field not in snapshot:
            raise ContractError(f"missing required root field: {field}")
        if not isinstance(snapshot[field], list):
            raise ContractError(f"{field} must be an array")

    generated_raw = snapshot.get("generated_at")
    generated_dt = _parse_aware_datetime(generated_raw, "generated_at")
    if generated_dt is None:
        raise ContractError("generated_at must be a parseable ISO 8601 timestamp with timezone")

    trends_note = snapshot.get("trends_note")
    if not isinstance(trends_note, str):
        raise ContractError("trends_note must be a string")

    intro_raw = snapshot.get("grokbot_intro")
    if not isinstance(intro_raw, dict):
        raise ContractError("grokbot_intro must be an object")

    timezone_value = snapshot.get("timezone")
    if "timezone" not in snapshot:
        timezone_value = DEFAULT_TIMEZONE
        warnings.add("root", f"timezone missing; defaulted to {DEFAULT_TIMEZONE}")
    elif not isinstance(timezone_value, str) or not timezone_value.strip():
        raise ContractError("timezone must be a non-empty string")
    else:
        timezone_value = timezone_value.strip()

    source_value = snapshot.get("source")
    if "source" not in snapshot:
        source_value = DEFAULT_SOURCE
        warnings.add("root", f"source missing; defaulted to {DEFAULT_SOURCE!r}")
    elif not isinstance(source_value, str) or not source_value.strip():
        raise ContractError("source must be a non-empty string")
    else:
        source_value = source_value.strip()

    posts_zh = _normalize_posts(snapshot["posts_zh"], "posts_zh", warnings)
    posts_en = _normalize_posts(snapshot["posts_en"], "posts_en", warnings)
    news = _normalize_news(snapshot["news"], warnings)
    trends = _normalize_trends(snapshot["trends_ai"], warnings)
    grokbot_posts = _normalize_posts(snapshot["grokbot_posts"], "grokbot_posts", warnings)
    intro, intro_fallback = _normalize_intro(intro_raw, warnings)

    arrays: dict[str, list[dict[str, Any]]] = {
        "posts_zh": posts_zh,
        "posts_en": posts_en,
        "news": news,
        "trends_ai": trends,
        "grokbot_posts": grokbot_posts,
    }

    file_bytes: dict[str, bytes] = {}
    files_sha256: dict[str, str] = {}
    for field in LIST_FIELDS:
        name = ARRAY_FILES[field]
        data = (json.dumps(arrays[field], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        file_bytes[name] = data
        files_sha256[name] = _sha256(data)

    counts = {field: len(arrays[field]) for field in LIST_FIELDS}
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_raw,
        "timezone": timezone_value,
        "source": source_value,
        "trends_note": trends_note,
        "grokbot_intro": intro,
        "grokbot_intro_fallback": intro_fallback,
        "summary": {
            "counts": counts,
            "warnings": len(warnings.items),
            "ignored_fields": warnings.ignored_fields,
        },
        "snapshot_sha256": _sha256(snapshot_bytes),
        "files_sha256": files_sha256,
    }
    meta_bytes = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    file_bytes[META_FILE] = meta_bytes

    return file_bytes, meta, warnings.items, warnings.ignored_fields


def _load_snapshot(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ContractError(f"snapshot not found: {path}") from exc
    except OSError as exc:
        raise ContractError(f"cannot read snapshot: {path}: {exc.strerror or exc}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"snapshot is not valid UTF-8: {path}") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(
            f"snapshot is not valid JSON: line {exc.lineno}, column {exc.colno}"
        ) from exc
    return parsed, data


def _output_targets(out_dir: Path) -> dict[str, Path]:
    return {name: out_dir / name for name in OUTPUT_FILES}


def _check_no_collision(snapshot_path: Path, out_dir: Path) -> None:
    snapshot_resolved = snapshot_path.resolve()
    for target in _output_targets(out_dir).values():
        if target.resolve() == snapshot_resolved:
            raise ContractError(
                f"input snapshot collides with an output file: {target}"
            )


def _write_outputs(out_dir: Path, file_bytes: dict[str, bytes]) -> None:
    """Write all outputs via same-directory temp files, with rollback on failure."""

    created_dir = False
    if not out_dir.exists():
        out_dir.mkdir(parents=True)
        created_dir = True
    elif not out_dir.is_dir():
        raise ContractError(f"output path is not a directory: {out_dir}")

    for name in OUTPUT_FILES:
        target = out_dir / name
        if target.is_symlink():
            raise ContractError(f"refusing to replace a symlink: {target}")
        if target.exists() and not target.is_file():
            raise ContractError(f"output target is not a regular file: {target}")

    temp_paths: dict[str, str] = {}
    backups: dict[str, bytes] = {}
    replaced: list[str] = []
    try:
        for name in OUTPUT_FILES:
            fd, temp_path = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=out_dir)
            temp_paths[name] = temp_path
            with os.fdopen(fd, "wb") as handle:
                handle.write(file_bytes[name])
                handle.flush()
                os.fsync(handle.fileno())

        for name in OUTPUT_FILES:
            target = out_dir / name
            if target.exists():
                backups[name] = target.read_bytes()
            os.replace(temp_paths[name], target)
            temp_paths.pop(name, None)
            replaced.append(name)
    except BaseException:
        for name in replaced:
            target = out_dir / name
            try:
                if name in backups:
                    restore_fd, restore_path = tempfile.mkstemp(
                        prefix=f".{name}.restore.", suffix=".tmp", dir=out_dir
                    )
                    with os.fdopen(restore_fd, "wb") as handle:
                        handle.write(backups[name])
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(restore_path, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        for temp_path in temp_paths.values():
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass
        if created_dir:
            try:
                if not any(out_dir.iterdir()):
                    out_dir.rmdir()
            except OSError:
                pass


def _format_counts(counts: dict[str, int]) -> str:
    return " ".join(f"{field}={counts[field]}" for field in LIST_FIELDS)


def _print_warnings(warnings: list[str], stream: Any = sys.stderr) -> None:
    for item in warnings:
        print(f"warning: {item}", file=stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize a local X public snapshot into data/*.json. "
            "No network calls and no X credentials."
        )
    )
    parser.add_argument(
        "--snapshot",
        default="data/latest.json",
        metavar="PATH",
        help="local UTF-8 snapshot (default: data/latest.json)",
    )
    parser.add_argument(
        "--out-dir",
        default="data",
        metavar="DIR",
        help="directory for the five array files and meta.json (default: data)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate and normalize in memory only; write nothing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    snapshot_path = Path(args.snapshot).expanduser()
    out_dir = Path(args.out_dir).expanduser()

    try:
        _check_no_collision(snapshot_path, out_dir)
        snapshot, snapshot_bytes = _load_snapshot(snapshot_path)
        file_bytes, meta, warnings, ignored = normalize_snapshot(snapshot, snapshot_bytes)
        counts = meta["summary"]["counts"]
        if args.check:
            print(
                f"check ok: {_format_counts(counts)} warnings={len(warnings)} "
                f"ignored_fields={ignored}"
            )
            _print_warnings(warnings)
            return 0

        _write_outputs(out_dir, file_bytes)
        print(
            f"wrote {len(OUTPUT_FILES)} files to {out_dir}: {_format_counts(counts)} "
            f"warnings={len(warnings)} ignored_fields={ignored}"
        )
        print(f"snapshot_sha256={meta['snapshot_sha256']} generated_at={meta['generated_at']}")
        _print_warnings(warnings)
        return 0
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: I/O failure: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # Defensive: never dump payloads or tracebacks in CI.
        print(f"error: unexpected normalization failure ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
