#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
RAW_DUMPS_DIR = ROOT / "raw" / "notion_dumps"
DEFAULT_NOTION_VERSION = "2022-06-28"
DEFAULT_MAX_QUERY_PAGES = 25


class NotionError(RuntimeError):
    pass


def load_env(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        raise NotionError(f"Missing env file: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class NotionClient:
    def __init__(self, token: str, notion_version: str = DEFAULT_NOTION_VERSION):
        self.token = token
        self.notion_version = notion_version

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"https://api.notion.com/v1/{path.lstrip('/')}"
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise NotionError(f"HTTP {exc.code} for {method} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise NotionError(f"Network error for {method} {path}: {exc}") from exc

    def retrieve_database(self, database_id: str) -> Dict[str, Any]:
        return self.request("GET", f"databases/{database_id}")

    def update_database(self, database_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("PATCH", f"databases/{database_id}", payload)

    def search(self, query: str, page_size: int = 10) -> Dict[str, Any]:
        return self.request(
            "POST",
            "search",
            {
                "query": query,
                "page_size": page_size,
                "filter": {"property": "object", "value": "page"},
            },
        )

    def query_database(self, database_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.request("POST", f"databases/{database_id}/query", payload or {})

    def retrieve_page(self, page_id: str) -> Dict[str, Any]:
        return self.request("GET", f"pages/{page_id}")

    def create_page(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "pages", payload)

    def update_page(self, page_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("PATCH", f"pages/{page_id}", payload)

    def retrieve_block_children(self, block_id: str, page_size: int = 100) -> Dict[str, Any]:
        return self.request("GET", f"blocks/{block_id}/children?page_size={page_size}")

    def append_block_children(
        self,
        block_id: str,
        children: List[Dict[str, Any]],
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"children": children}
        if after:
            payload["after"] = after
        return self.request("PATCH", f"blocks/{block_id}/children", payload)

    def delete_block(self, block_id: str) -> Dict[str, Any]:
        return self.request("DELETE", f"blocks/{block_id}")

    def update_block(self, block_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("PATCH", f"blocks/{block_id}", payload)


def require_env(env: Dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise NotionError(f"Missing required env var: {key}")
    return value


def optional_env(env: Dict[str, str], key: str) -> Optional[str]:
    value = env.get(key, "").strip()
    return value or None


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def timestamp_slug() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def daily_log_filename(suffix: str) -> str:
    return f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')}-{suffix}"


def short_notion_id(value: str) -> str:
    normalized = normalize_notion_id(value)
    return normalized[:8] if normalized else "unknown"


def ensure_raw_dumps_dir() -> Path:
    RAW_DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_DUMPS_DIR


def write_json_snapshot(filename: str, payload: Dict[str, Any]) -> Path:
    dump_dir = ensure_raw_dumps_dir()
    path = dump_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def append_jsonl_log(filename: str, payload: Dict[str, Any]) -> Path:
    dump_dir = ensure_raw_dumps_dir()
    path = dump_dir / filename
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def append_audit_event(payload: Dict[str, Any]) -> Path:
    return append_jsonl_log(daily_log_filename("audit-log.jsonl"), payload)


def load_mapping(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    mapping_path = Path(path)
    if not mapping_path.is_absolute():
        mapping_path = ROOT / mapping_path
    if not mapping_path.exists():
        raise NotionError(f"Mapping file not found: {mapping_path}")
    return json.loads(mapping_path.read_text(encoding="utf-8"))


def detect_title_property(database: Dict[str, Any]) -> Optional[str]:
    for name, meta in database.get("properties", {}).items():
        if meta.get("type") == "title":
            return name
    return None


def database_parent_id(page: Dict[str, Any]) -> Optional[str]:
    parent = page.get("parent", {})
    if parent.get("type") == "database_id":
        return parent.get("database_id")
    return None


def normalize_notion_id(value: Optional[str]) -> str:
    if not value:
        return ""
    return value.replace("-", "").lower()


def rich_text_value(text: str) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}}]


def chunk_text(text: str, max_len: int = 1800) -> List[str]:
    if len(text) <= max_len:
        return [text]
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_len, len(text))
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start:
                end = newline
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks or [text[:max_len]]


def title_property_payload(text: str) -> Dict[str, Any]:
    return {"title": rich_text_value(text)}


def extract_title(page: Dict[str, Any], title_property: str) -> str:
    prop = page.get("properties", {}).get(title_property, {})
    chunks = prop.get("title", [])
    return "".join(chunk.get("plain_text", "") for chunk in chunks).strip()


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def rich_text_plain_text(chunks: List[Dict[str, Any]]) -> str:
    return "".join(chunk.get("plain_text", "") for chunk in chunks).strip()


def extract_property_text(page: Dict[str, Any], property_name: str) -> str:
    prop = page.get("properties", {}).get(property_name, {})
    prop_type = prop.get("type")
    if prop_type == "title":
        return rich_text_plain_text(prop.get("title", []))
    if prop_type == "rich_text":
        return rich_text_plain_text(prop.get("rich_text", []))
    if prop_type == "url":
        return prop.get("url") or ""
    if prop_type == "email":
        return prop.get("email") or ""
    if prop_type == "phone_number":
        return prop.get("phone_number") or ""
    if prop_type == "select":
        value = prop.get("select")
        return value.get("name", "") if value else ""
    if prop_type == "status":
        value = prop.get("status")
        return value.get("name", "") if value else ""
    if prop_type == "multi_select":
        values = prop.get("multi_select", []) or []
        return ", ".join(v.get("name", "") for v in values if v.get("name"))
    if prop_type == "number":
        value = prop.get("number")
        return "" if value is None else str(value)
    if prop_type == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    if prop_type == "date":
        value = prop.get("date")
        if not value:
            return ""
        start = value.get("start") or ""
        end = value.get("end")
        return f"{start} – {end}" if end else start
    if prop_type == "people":
        values = prop.get("people", []) or []
        return ", ".join(p.get("name") or p.get("id", "") for p in values)
    if prop_type == "relation":
        values = prop.get("relation", []) or []
        return ", ".join(v.get("id", "") for v in values)
    if prop_type == "files":
        values = prop.get("files", []) or []
        names = [v.get("name", "") for v in values if v.get("name")]
        return ", ".join(names)
    if prop_type == "unique_id":
        value = prop.get("unique_id")
        if not value:
            return ""
        prefix = value.get("prefix") or ""
        number = value.get("number")
        return f"{prefix}{number}" if number is not None else ""
    if prop_type == "formula":
        value = prop.get("formula") or {}
        formula_type = value.get("type")
        if formula_type == "string":
            return value.get("string") or ""
        if formula_type == "number":
            num = value.get("number")
            return "" if num is None else str(num)
        if formula_type == "boolean":
            return "true" if value.get("boolean") else "false"
        if formula_type == "date":
            inner = value.get("date") or {}
            return inner.get("start") or ""
        return ""
    if prop_type == "rollup":
        value = prop.get("rollup") or {}
        rollup_type = value.get("type")
        if rollup_type == "number":
            num = value.get("number")
            return "" if num is None else str(num)
        if rollup_type == "date":
            inner = value.get("date") or {}
            return inner.get("start") or ""
        if rollup_type == "array":
            parts: List[str] = []
            for item in value.get("array", []) or []:
                item_type = item.get("type")
                if item_type == "title":
                    parts.append(rich_text_plain_text(item.get("title", [])))
                elif item_type == "rich_text":
                    parts.append(rich_text_plain_text(item.get("rich_text", [])))
                elif item_type == "number":
                    num = item.get("number")
                    if num is not None:
                        parts.append(str(num))
            return ", ".join(p for p in parts if p)
        return ""
    if prop_type == "created_time":
        return prop.get("created_time") or ""
    if prop_type == "last_edited_time":
        return prop.get("last_edited_time") or ""
    return ""


def extract_block_text(block: Dict[str, Any]) -> str:
    block_type = block.get("type")
    if not block_type:
        return ""
    block_value = block.get(block_type, {})
    if "rich_text" in block_value:
        return rich_text_plain_text(block_value.get("rich_text", []))
    if block_type == "bookmark":
        return block_value.get("url", "")
    return ""


def iterate_block_children(
    client: NotionClient,
    block_id: str,
    page_size: int = 100,
    max_pages: int = DEFAULT_MAX_QUERY_PAGES,
) -> List[Dict[str, Any]]:
    path = f"blocks/{block_id}/children?page_size={page_size}"
    results: List[Dict[str, Any]] = []
    page_count = 0
    while True:
        response = client.request("GET", path)
        results.extend(response.get("results", []))
        page_count += 1
        if not response.get("has_more"):
            break
        if page_count >= max_pages:
            raise NotionError(
                f"Exceeded block pagination limit ({max_pages}) for block {block_id}; "
                "narrow the input or raise the max_pages limit"
            )
        next_cursor = response.get("next_cursor")
        path = f"blocks/{block_id}/children?page_size={page_size}&start_cursor={next_cursor}"
    return results


def flatten_block_texts(client: NotionClient, block_id: str) -> List[str]:
    lines: List[str] = []
    for block in iterate_block_children(client, block_id):
        text = extract_block_text(block)
        if text:
            lines.append(text)
        if block.get("has_children") and block.get("id"):
            lines.extend(flatten_block_texts(client, block["id"]))
    return lines


def read_page_body_text(client: NotionClient, page_id: str) -> str:
    return "\n\n".join(flatten_block_texts(client, page_id)).strip()


def extract_heading_structure(blocks: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Return ordered list of (heading_type, heading_text) tuples for heading_2 / heading_3 blocks."""
    out: List[Tuple[str, str]] = []
    for block in blocks:
        btype = block.get("type")
        if btype in ("heading_2", "heading_3"):
            text = rich_text_plain_text(block.get(btype, {}).get("rich_text", [])).strip()
            if text:
                out.append((btype, text))
    return out


def normalize_heading_text(text: str) -> str:
    """Strip trailing ISO date stamp (e.g. "结构化整理 2026-04-21" -> "结构化整理") and normalize spacing."""
    if not text:
        return ""
    stripped = re.sub(r"\s*\d{4}-\d{2}-\d{2}\s*$", "", text).strip()
    return stripped


def conceptual_heading_set(blocks: List[Dict[str, Any]]) -> List[str]:
    """Return deduped list of conceptual headings (heading_2 ∪ heading_3), date-stamps stripped."""
    seen: set = set()
    out: List[str] = []
    for btype, text in extract_heading_structure(blocks):
        if btype not in ("heading_2", "heading_3"):
            continue
        normalized = normalize_heading_text(text)
        if not normalized:
            continue
        key = normalize(normalized)
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


PLACEHOLDER_MARKER = "<placeholder>"


def is_placeholder_page(body_text: str) -> bool:
    if not body_text:
        return False
    return body_text.strip().startswith(PLACEHOLDER_MARKER)


def find_section_body(
    blocks: List[Dict[str, Any]],
    heading_text: str,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Locate a top-level heading_2 or heading_3 by text and return (heading_block, body_blocks).

    body_blocks are all subsequent top-level blocks until the next heading_2 or heading_3
    (whichever comes first), or end of blocks. Returns (None, []) if not found.
    """
    normalized_target = normalize(heading_text)
    heading_block: Optional[Dict[str, Any]] = None
    body: List[Dict[str, Any]] = []
    collecting = False
    for block in blocks:
        btype = block.get("type")
        if btype in ("heading_2", "heading_3"):
            text = rich_text_plain_text(block.get(btype, {}).get("rich_text", []))
            if collecting:
                break
            if normalize(text) == normalized_target:
                heading_block = block
                collecting = True
                continue
        elif collecting:
            body.append(block)
    return heading_block, body


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


BODY_TEXT_SNAPSHOT_LIMIT = 8000


def truncate_body_text(value: str, limit: int = BODY_TEXT_SNAPSHOT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n…[truncated]"


def compute_paragraph_diff(old_body: str, new_body: str, context: int = 1) -> str:
    old_paras = [p.strip() for p in re.split(r"\n\s*\n", old_body) if p.strip()]
    new_paras = [p.strip() for p in re.split(r"\n\s*\n", new_body) if p.strip()]
    diff_iter = difflib.unified_diff(
        old_paras,
        new_paras,
        fromfile="raw@prev",
        tofile="raw@now",
        lineterm="",
        n=context,
    )
    return "\n".join(diff_iter)


def today_iso_date() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def find_last_successful_compile(raw_page_id: str) -> Optional[Dict[str, Any]]:
    dump_dir = ensure_raw_dumps_dir()
    for path in sorted(dump_dir.glob("*-audit-log.jsonl"), reverse=True):
        for event in reversed(load_jsonl(path)):
            if event.get("command") != "compile-from-raw":
                continue
            if event.get("status") != "success":
                continue
            if normalize_notion_id(event.get("raw_page_id")) != normalize_notion_id(raw_page_id):
                continue
            return event
    return None


def find_prior_compile_by_body_hash(body_hash: str, exclude_raw_page_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    dump_dir = ensure_raw_dumps_dir()
    exclude_norm = normalize_notion_id(exclude_raw_page_id) if exclude_raw_page_id else ""
    for path in sorted(dump_dir.glob("*-audit-log.jsonl"), reverse=True):
        for event in reversed(load_jsonl(path)):
            if event.get("command") != "compile-from-raw":
                continue
            if event.get("status") != "success":
                continue
            if event.get("body_hash") != body_hash:
                continue
            if exclude_norm and normalize_notion_id(event.get("raw_page_id")) == exclude_norm:
                continue
            return event
    return None


def find_last_compile_queue_failures() -> List[str]:
    dump_dir = ensure_raw_dumps_dir()
    for path in sorted(dump_dir.glob("*-audit-log.jsonl"), reverse=True):
        for event in reversed(load_jsonl(path)):
            if event.get("command") != "compile-queue":
                continue
            failures = event.get("failures") or []
            return [f.get("raw_page_id") for f in failures if f.get("raw_page_id")]
    return []


def detect_command_name(argv: List[str]) -> str:
    known_commands = {
        "inspect-schema",
        "search",
        "compile-from-raw",
        "compile-queue",
        "upsert-note",
        "lint",
        "log-session-event",
        "cleanup-wiki-page",
        "check-editorial",
        "consolidate-evidence",
        "reference-check",
        "seed-related-pages",
        "rewrite-section",
        "link-pages",
        "link-concepts-in-page",
    }
    for token in argv:
        if token in known_commands:
            return token
    return "unknown"


def query_database_pages(
    client: NotionClient,
    database_id: str,
    filter_body: Optional[Dict[str, Any]],
    page_size: int = 20,
    max_pages: int = DEFAULT_MAX_QUERY_PAGES,
) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"page_size": page_size}
    if filter_body:
        payload["filter"] = filter_body
    results: List[Dict[str, Any]] = []
    page_count = 0
    while True:
        response = client.query_database(database_id, payload)
        results.extend(response.get("results", []))
        page_count += 1
        if not response.get("has_more"):
            break
        if page_count >= max_pages:
            raise NotionError(
                f"Exceeded query page limit ({max_pages}) for database {database_id}; "
                "refine the filter or raise the max_pages limit"
            )
        payload["start_cursor"] = response.get("next_cursor")
    return results


def dedupe_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for page in pages:
        page_id = page.get("id")
        if page_id and page_id not in unique:
            unique[page_id] = page
    return list(unique.values())


def split_aliases(raw_value: str) -> List[str]:
    if not raw_value:
        return []
    parts = re.split(r"[,;\n|/、，；]+", raw_value)
    aliases = [normalize(part) for part in parts if normalize(part)]
    whole = normalize(raw_value)
    if whole and whole not in aliases:
        aliases.append(whole)
    return aliases


def extract_unique_id_number(raw_value: str) -> Optional[int]:
    if not raw_value:
        return None
    match = re.search(r"(\d+)$", raw_value.strip())
    if not match:
        return None
    return int(match.group(1))


def page_matches_canonical_id(
    page: Dict[str, Any],
    canonical_id: str,
    canonical_property: str,
    canonical_property_type: str,
) -> bool:
    if canonical_property_type == "unique_id":
        candidate_number = extract_unique_id_number(canonical_id)
        page_unique = page.get("properties", {}).get(canonical_property, {}).get("unique_id", {})
        return candidate_number is not None and page_unique.get("number") == candidate_number
    return normalize(extract_property_text(page, canonical_property)) == normalize(canonical_id)


def page_matches_query(
    page: Dict[str, Any],
    query: str,
    title_property: str,
    aliases_property: Optional[str] = None,
) -> bool:
    normalized_query = normalize(query)
    if normalize(extract_title(page, title_property)) == normalized_query:
        return True
    if aliases_property:
        aliases_text = extract_property_text(page, aliases_property)
        if normalized_query in split_aliases(aliases_text):
            return True
    return False


def classify_page_match(
    page: Dict[str, Any],
    title: str,
    title_property: str,
    aliases_property: Optional[str],
    canonical_id: Optional[str],
    canonical_property: Optional[str],
    canonical_property_type: str,
) -> Optional[str]:
    if canonical_id and canonical_property:
        if page_matches_canonical_id(page, canonical_id, canonical_property, canonical_property_type):
            return "canonical_id"
    if normalize(extract_title(page, title_property)) == normalize(title):
        return "title"
    if aliases_property:
        aliases_text = extract_property_text(page, aliases_property)
        if normalize(title) in split_aliases(aliases_text):
            return "alias"
    return None


def find_fuzzy_candidates(
    client: NotionClient,
    database_id: str,
    title: str,
    title_property: str,
    title_property_type: str,
    exclude_page_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    tokens: List[str] = []
    seen_tokens: set = set()
    for raw in re.split(r"\s+|[,;、，；/:：]+", title):
        tok = raw.strip()
        if len(tok) < 2:
            continue
        key = normalize(tok)
        if key in seen_tokens:
            continue
        seen_tokens.add(key)
        tokens.append(tok)
    if not tokens:
        return []
    exclude_norm = {normalize_notion_id(p) for p in (exclude_page_ids or [])}
    collected: Dict[str, Dict[str, Any]] = {}
    for tok in tokens[:5]:
        filter_body = build_contains_filter(title_property, title_property_type, tok)
        if not filter_body:
            continue
        try:
            pages = query_database_pages(client, database_id, filter_body, page_size=10, max_pages=3)
        except NotionError:
            continue
        for page in pages:
            pid = page.get("id")
            if not pid or normalize_notion_id(pid) in exclude_norm:
                continue
            if pid not in collected:
                collected[pid] = page
    return list(collected.values())


def find_pages_by_canonical_id(
    client: NotionClient,
    database_id: str,
    canonical_id: str,
    canonical_property: str,
    canonical_property_type: str,
) -> List[Dict[str, Any]]:
    filter_body: Optional[Dict[str, Any]] = None
    if canonical_property_type == "rich_text":
        filter_body = {"property": canonical_property, "rich_text": {"equals": canonical_id}}
    elif canonical_property_type == "title":
        filter_body = {"property": canonical_property, "title": {"equals": canonical_id}}
    elif canonical_property_type == "unique_id":
        unique_id_number = extract_unique_id_number(canonical_id)
        if unique_id_number is not None:
            filter_body = {"property": canonical_property, "unique_id": {"equals": unique_id_number}}
    elif canonical_property_type:
        print(
            f"WARN: canonical property type {canonical_property_type!r} does not support direct filtering; "
            "falling back to local scan",
            file=sys.stderr,
        )

    pages = query_database_pages(client, database_id, filter_body)
    matches = [
        page
        for page in pages
        if page_matches_canonical_id(page, canonical_id, canonical_property, canonical_property_type)
    ]
    return dedupe_pages(matches)


def build_contains_filter(property_name: str, property_type: str, query: str) -> Optional[Dict[str, Any]]:
    if property_type == "title":
        return {"property": property_name, "title": {"contains": query}}
    if property_type == "rich_text":
        return {"property": property_name, "rich_text": {"contains": query}}
    if property_type == "multi_select":
        return {"property": property_name, "multi_select": {"contains": query}}
    return None


def search_in_database(
    client: NotionClient,
    database_id: str,
    query: str,
    title_property: str,
    title_property_type: str = "title",
    aliases_property: Optional[str] = None,
    aliases_property_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    filters: List[Dict[str, Any]] = []
    title_filter = build_contains_filter(title_property, title_property_type, query)
    if title_filter:
        filters.append(title_filter)
    if aliases_property and aliases_property_type:
        aliases_filter = build_contains_filter(aliases_property, aliases_property_type, query)
        if aliases_filter:
            filters.append(aliases_filter)

    if not filters:
        return []

    pages = dedupe_pages(
        query_database_pages(client, database_id, {"or": filters})
        if len(filters) > 1
        else query_database_pages(client, database_id, filters[0])
    )
    pages.sort(
        key=lambda page: (
            0 if normalize(extract_title(page, title_property)) == normalize(query) else 1,
            extract_title(page, title_property).lower(),
        )
    )
    return pages


def property_payload_for_value(prop_meta: Dict[str, Any], value: Any) -> Dict[str, Any]:
    prop_type = prop_meta.get("type")
    if prop_type == "title":
        return title_property_payload(str(value))
    if prop_type == "rich_text":
        return {"rich_text": rich_text_value(str(value))}
    if prop_type == "number":
        return {"number": float(value)}
    if prop_type == "status":
        return {"status": {"name": str(value)}}
    if prop_type == "select":
        return {"select": {"name": str(value)}}
    if prop_type == "multi_select":
        values = value if isinstance(value, list) else [value]
        return {"multi_select": [{"name": str(item)} for item in values]}
    if prop_type == "date":
        return {"date": {"start": str(value)}}
    if prop_type == "url":
        return {"url": str(value)}
    if prop_type == "relation":
        values = value if isinstance(value, list) else [value]
        return {"relation": [{"id": str(item)} for item in values]}
    raise NotionError(f"Unsupported property type for automatic write: {prop_type}")


def relation_targets_database(prop_meta: Dict[str, Any], expected_database_id: str) -> bool:
    relation = prop_meta.get("relation", {})
    actual_database_id = relation.get("database_id")
    return normalize_notion_id(actual_database_id) == normalize_notion_id(expected_database_id)


def build_properties(database: Dict[str, Any], mapping: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    props_meta = database.get("properties", {})
    title_name = args.title_property or mapping.get("title_property") or detect_title_property(database)
    if not title_name or title_name not in props_meta:
        raise NotionError("Unable to determine title property")

    properties: Dict[str, Any] = {title_name: title_property_payload(args.title)}
    optional_writes: List[Tuple[Optional[str], Any]] = [
        (args.canonical_id_property or mapping.get("canonical_id_property"), args.canonical_id),
        (args.verification_property or mapping.get("verification_property"), args.verification),
        (args.compounded_level_property or mapping.get("compounded_level_property"), args.compounded_level),
        (args.last_compounded_at_property or mapping.get("last_compounded_at_property"), args.last_compounded_at or iso_now()),
    ]
    for prop_name, value in optional_writes:
        if not prop_name or value is None:
            continue
        meta = props_meta.get(prop_name)
        if not meta:
            continue
        properties[prop_name] = property_payload_for_value(meta, value)
    return properties


def build_append_blocks(note: str, heading: str, source_url: Optional[str]) -> List[Dict[str, Any]]:
    timestamp = iso_now()
    blocks: List[Dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": rich_text_value(f"{heading} {timestamp[:10]}")},
        }
    ]
    for chunk in chunk_text(note):
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": rich_text_value(chunk)},
            }
        )
    if source_url:
        blocks.append(
            {
                "object": "block",
                "type": "bookmark",
                "bookmark": {"url": source_url},
            }
        )
    return blocks


def infer_semantic_title(raw_title: str) -> str:
    title = raw_title.strip()
    chapter_match = re.match(r"^第\s*\d+\s*章\s*(.+)$", title)
    if chapter_match:
        title = chapter_match.group(1).strip()
    for delimiter in ("：", ":"):
        if delimiter in title:
            left, _right = title.split(delimiter, 1)
            left = left.strip()
            if 1 < len(left) <= 80:
                return left
    return title


def merge_alias_values(*groups: str) -> str:
    seen = set()
    ordered: List[str] = []
    for group in groups:
        if not group:
            continue
        parts = [part.strip() for part in re.split(r"[,;\n|/、，；]+", group) if part.strip()]
        if not parts:
            parts = [group.strip()]
        if len(parts) == 1 and parts[0] != group.strip():
            parts.append(group.strip())
        elif len(parts) == 1:
            parts = [group.strip()]
        for alias in parts:
            key = normalize(alias)
            if key and key not in seen:
                seen.add(key)
                ordered.append(alias)
    return ", ".join(ordered)


def infer_topics(title: str, note: str) -> str:
    haystack = normalize(f"{title}\n{note}")
    topic_map = [
        ("query loop", "Query Loop"),
        ("queryengine", "Query Engine"),
        ("agent", "Agent Runtime"),
        ("runtime", "Agent Runtime"),
        ("state", "State Management"),
        ("session", "State Management"),
        ("tool_use", "Tool Orchestration"),
        ("tool", "Tool Orchestration"),
        ("prompt-too-long", "Recovery Logic"),
        ("max-output", "Recovery Logic"),
        ("恢复", "Recovery Logic"),
        ("context", "Context Governance"),
        ("compact", "Context Governance"),
        ("history snip", "Context Governance"),
        ("中断", "Interrupt Handling"),
    ]
    topics: List[str] = []
    for needle, label in topic_map:
        if needle in haystack and label not in topics:
            topics.append(label)
    return " / ".join(topics[:4])


def first_nonempty_paragraphs(note: str, limit: int = 3) -> List[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", note) if part.strip()]
    return paragraphs[:limit]


def sentence_excerpt(text: str, limit: int = 180) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    clipped = cleaned[:limit].rstrip()
    for delimiter in ("。", "；", ".", ";", "，", ",", " "):
        idx = clipped.rfind(delimiter)
        if idx > 40:
            clipped = clipped[:idx]
            break
    return clipped + "…"


def infer_key_points(title: str, note: str) -> List[str]:
    haystack = normalize(f"{title}\n{note}")
    candidates = [
        ("跨轮状态", ["状态", "turn", "session", "state"]),
        ("输入治理先于推理", ["治理", "compact", "history snip", "context collapse"]),
        ("模型输出被当作事件流处理", ["事件流", "stream", "for await", "tool_use"]),
        ("中断后需要补齐执行账本", ["中断", "abort", "synthetic tool_result"]),
        ("恢复是运行时主路径的一部分", ["恢复", "prompt-too-long", "max-output"]),
        ("停止条件需要区分完成、失败、恢复和继续", ["停止条件", "stop hook", "retry", "继续"]),
    ]
    points: List[str] = []
    for label, needles in candidates:
        if any(normalize(needle) in haystack for needle in needles):
            points.append(label)
    return points[:4]


def infer_related_concepts(title: str, note: str) -> List[str]:
    haystack = normalize(f"{title}\n{note}")
    concept_map = [
        ("QueryEngine", ["queryengine"]),
        ("Agent Runtime", ["agent", "runtime"]),
        ("State Management", ["状态", "state", "session", "turn"]),
        ("Context Governance", ["context", "compact", "history snip", "micro compact", "context collapse"]),
        ("Recovery Logic", ["恢复", "prompt-too-long", "max-output"]),
        ("Interrupt Handling", ["中断", "abort", "synthetic tool_result"]),
        ("Tool Orchestration", ["tool_use", "tool", "streamingtoolexecutor"]),
    ]
    concepts: List[str] = []
    for label, needles in concept_map:
        if any(normalize(needle) in haystack for needle in needles):
            concepts.append(label)
    return concepts[:6]


def infer_evidence_quotes(note: str, limit: int = 4) -> List[str]:
    paragraphs = first_nonempty_paragraphs(note, limit=10)
    evidence: List[str] = []
    for paragraph in paragraphs:
        excerpt = sentence_excerpt(paragraph, limit=110)
        if excerpt and excerpt not in evidence:
            evidence.append(excerpt)
        if len(evidence) >= limit:
            break
    return evidence


def infer_core_judgment(title: str, note: str) -> str:
    return f"{title} 更适合作为一个长期维护的知识对象，而不是一次性章节摘要。"


def infer_implementation_signals(title: str, note: str) -> List[str]:
    haystack = normalize(f"{title}\n{note}")
    signal_map = [
        ("显式跨轮状态对象", ["state", "messages", "toolusecontext", "turncount"]),
        ("模型调用前的输入治理链路", ["compact", "history snip", "context collapse", "micro compact"]),
        ("流式事件消费而非一次性响应", ["for await", "stream", "tool_use"]),
        ("中断后补齐 tool_result", ["synthetic tool_result", "abort", "missing tool result"]),
        ("失败进入恢复分支而不是礼貌终止", ["prompt-too-long", "max-output", "恢复"]),
    ]
    signals: List[str] = []
    for label, needles in signal_map:
        if any(normalize(needle) in haystack for needle in needles):
            signals.append(label)
    return signals[:5]


def infer_neighbor_distinction(title: str, note: str) -> str:
    return f"{title} 应被视作一个独立知识对象，与相邻条目的边界由后续人工或会话层判断细化。"


def build_structured_refinement_blocks(title: str, note: str, raw_title: str) -> List[Dict[str, Any]]:
    paragraphs = first_nonempty_paragraphs(note, limit=3)
    definition_source = paragraphs[0] if paragraphs else note
    importance_source = paragraphs[1] if len(paragraphs) > 1 else definition_source
    key_points = infer_key_points(title, note)
    blocks: List[Dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": rich_text_value(f"结构化整理 {today_iso_date()}")},
        },
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": rich_text_value("定义")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": rich_text_value(
                    f"原文摘要：{sentence_excerpt(definition_source)}"
                )
            },
        },
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": rich_text_value("为什么重要")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": rich_text_value(
                    f"原文要点：{sentence_excerpt(importance_source)}"
                )
            },
        },
    ]
    if key_points:
        blocks.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": rich_text_value("关键机制")},
            }
        )
        for point in key_points:
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": rich_text_value(point)},
                }
            )
    blocks.append(
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": rich_text_value(f"原始来源标题保留为：{raw_title}")
            },
        }
    )
    return blocks


def build_deepening_blocks(title: str, note: str) -> List[Dict[str, Any]]:
    related_concepts = infer_related_concepts(title, note)
    evidence_quotes = infer_evidence_quotes(note)
    implementation_signals = infer_implementation_signals(title, note)
    core_judgment = infer_core_judgment(title, note)
    neighbor_distinction = infer_neighbor_distinction(title, note)
    blocks: List[Dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": rich_text_value(f"补充整理 {today_iso_date()}")},
        },
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": rich_text_value("核心判断")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text_value(core_judgment)},
        }
    ]
    if implementation_signals:
        blocks.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": rich_text_value("实现信号")},
            }
        )
        for signal in implementation_signals:
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": rich_text_value(signal)},
                }
            )
    if related_concepts:
        blocks.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": rich_text_value("关联概念")},
            }
        )
        for concept in related_concepts:
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": rich_text_value(concept)},
                }
            )
    blocks.append(
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": rich_text_value("与相邻概念的区别")},
        }
    )
    blocks.append(
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text_value(neighbor_distinction)},
        }
    )
    if evidence_quotes:
        blocks.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": rich_text_value("原文证据")},
            }
        )
        for quote in evidence_quotes:
            blocks.append(
                {
                    "object": "block",
                    "type": "quote",
                    "quote": {"rich_text": rich_text_value(quote)},
                }
            )
    return blocks


def resolve_title_property_name(database: Dict[str, Any], mapping: Dict[str, Any], cli_value: Optional[str]) -> str:
    title_prop = cli_value or mapping.get("title_property") or detect_title_property(database)
    if not title_prop:
        raise NotionError("Unable to determine title property")
    return title_prop


def find_upsert_target(
    client: NotionClient,
    database: Dict[str, Any],
    database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    """Resolve which wiki page args.title / args.canonical_id should land on.

    Returns a dict with exact_match, match_strategy, candidates,
    fuzzy_candidate_ids, title_prop / title_prop_type. Raises NotionError
    on --strict-alias / --strict-fuzzy / multi-candidate violations.
    Does not write to Notion.
    """
    title_prop = resolve_title_property_name(database, mapping, args.title_property)
    title_prop_type = database.get("properties", {}).get(title_prop, {}).get("type", "title")
    aliases_prop = mapping.get("aliases_property")
    aliases_prop_type = None
    if aliases_prop not in database.get("properties", {}):
        aliases_prop = None
    else:
        aliases_prop_type = database.get("properties", {}).get(aliases_prop, {}).get("type")
    canonical_prop = args.canonical_id_property or mapping.get("canonical_id_property")
    canonical_prop_type = None
    if canonical_prop not in database.get("properties", {}):
        canonical_prop = None
    else:
        canonical_prop_type = database.get("properties", {}).get(canonical_prop, {}).get("type")

    candidates: List[Dict[str, Any]] = []
    if args.canonical_id and canonical_prop:
        candidates = find_pages_by_canonical_id(
            client,
            database_id,
            args.canonical_id,
            canonical_prop,
            canonical_prop_type or "",
        )
    if not candidates:
        if args.canonical_id and canonical_prop:
            print(
                f"WARN: canonical_id={args.canonical_id!r} not matched in wiki; falling back to title/aliases search",
                file=sys.stderr,
            )
        candidates = search_in_database(
            client,
            database_id,
            args.title,
            title_prop,
            title_prop_type,
            aliases_prop,
            aliases_prop_type,
        )

    matched_candidates: List[Tuple[Dict[str, Any], str]] = []
    for page in candidates:
        match_strategy = classify_page_match(
            page,
            args.title,
            title_prop,
            aliases_prop,
            args.canonical_id,
            canonical_prop,
            canonical_prop_type or "",
        )
        if match_strategy:
            matched_candidates.append((page, match_strategy))

    for strategy in ("canonical_id", "title", "alias"):
        strategy_hits = [item for item in matched_candidates if item[1] == strategy]
        if len(strategy_hits) > 1:
            raise NotionError(
                f"Multiple wiki candidates matched via {strategy}: "
                + ", ".join(
                    f"{extract_title(page, title_prop)}<{page.get('id')}>"
                    for page, _ in strategy_hits
                )
            )

    exact_match = matched_candidates[0][0] if matched_candidates else None
    match_strategy = matched_candidates[0][1] if matched_candidates else "created"

    if match_strategy == "alias" and getattr(args, "strict_alias", False):
        raise NotionError(
            f"Alias match on wiki page {exact_match.get('id')!r} requires explicit confirmation; "
            "rerun without --strict-alias, or adjust Canonical ID/title first."
        )
    if match_strategy == "alias":
        print(
            f"WARN: matched via alias on wiki page {exact_match.get('id')!r}; "
            "review_required=true recorded. Use --strict-alias to hard-stop on alias hits.",
            file=sys.stderr,
        )

    fuzzy_candidate_ids: List[str] = []
    if not exact_match:
        fuzzy_candidates = find_fuzzy_candidates(
            client,
            database_id,
            args.title,
            title_prop,
            title_prop_type,
            exclude_page_ids=[p.get("id") for p, _ in matched_candidates],
        )
        fuzzy_candidate_ids = [p.get("id") for p in fuzzy_candidates if p.get("id")]
        if fuzzy_candidates and getattr(args, "strict_fuzzy", False):
            preview = ", ".join(
                f"{extract_title(p, title_prop)}<{p.get('id')}>" for p in fuzzy_candidates[:5]
            )
            raise NotionError(
                f"No tier 1-3 match for {args.title!r} but {len(fuzzy_candidates)} fuzzy candidate(s) exist: "
                f"{preview}. Rerun without --strict-fuzzy after review or set Canonical ID/title first."
            )
        if fuzzy_candidates:
            print(
                f"WARN: no tier 1-3 match but {len(fuzzy_candidates)} fuzzy candidate(s) exist; "
                "proceeding to create new page. Use --strict-fuzzy to hard-stop.",
                file=sys.stderr,
            )

    return {
        "exact_match": exact_match,
        "match_strategy": match_strategy,
        "candidates": candidates,
        "fuzzy_candidate_ids": fuzzy_candidate_ids,
        "title_prop": title_prop,
        "title_prop_type": title_prop_type,
    }


def upsert_note_to_wiki(
    client: NotionClient,
    database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    database = client.retrieve_database(database_id)
    target = find_upsert_target(client, database, database_id, mapping, args)
    exact_match = target["exact_match"]
    match_strategy = target["match_strategy"]
    candidates = target["candidates"]
    fuzzy_candidate_ids = target["fuzzy_candidate_ids"]

    append_heading = args.append_heading or mapping.get("append_heading", "增量更新")
    if exact_match:
        properties = build_properties(database, mapping, args)
        if args.increment_compounded_level:
            level_prop_name = args.compounded_level_property or mapping.get("compounded_level_property")
            if level_prop_name and level_prop_name in database.get("properties", {}):
                current_number = exact_match.get("properties", {}).get(level_prop_name, {}).get("number") or 0
                properties[level_prop_name] = {"number": current_number + 1}
        client.update_page(exact_match["id"], {"properties": properties})
        blocks = build_append_blocks(args.note, append_heading, args.source_url)
        client.append_block_children(exact_match["id"], blocks)
        return {
            "action": "updated",
            "page_id": exact_match["id"],
            "title": args.title,
            "match_strategy": match_strategy,
            "candidate_count": len(candidates),
            "review_required": match_strategy == "alias",
        }

    payload: Dict[str, Any] = {
        "parent": {"database_id": database_id},
        "properties": build_properties(database, mapping, args),
        "children": build_append_blocks(args.note, append_heading, args.source_url),
    }
    created = client.create_page(payload)
    return {
        "action": "created",
        "page_id": created.get("id"),
        "title": args.title,
        "match_strategy": match_strategy,
        "candidate_count": len(candidates),
        "fuzzy_candidate_ids": fuzzy_candidate_ids,
    }


def audit_success(command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    event = {
        "timestamp": iso_now(),
        "command": command,
        "status": "success",
        **payload,
    }
    audit_path = append_audit_event(event)
    event["audit_log_path"] = str(audit_path)
    return event


def finalize_compile_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    log_path = append_jsonl_log(daily_log_filename("compile-log.jsonl"), payload)
    payload["log_path"] = str(log_path)
    return audit_success("compile-from-raw", payload)


def apply_low_risk_refinement(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    wiki_page_id: str,
    raw_title: str,
    note: str,
    wiki_action: str,
) -> Dict[str, Any]:
    database = client.retrieve_database(wiki_database_id)
    page = client.retrieve_page(wiki_page_id)
    props_meta = database.get("properties", {})
    title_prop = mapping.get("title_property") or detect_title_property(database)
    if not title_prop or title_prop not in props_meta:
        raise NotionError("Unable to determine wiki title property for refinement")

    current_title = extract_title(page, title_prop)
    semantic_title = infer_semantic_title(raw_title)
    aliases_prop = mapping.get("aliases_property")
    topic_prop = mapping.get("topic_property")
    compounded_level_prop = mapping.get("compounded_level_property")
    last_compounded_at_prop = mapping.get("last_compounded_at_property")
    existing_aliases = extract_property_text(page, aliases_prop) if aliases_prop else ""
    existing_body_text = read_page_body_text(client, wiki_page_id)

    property_updates: Dict[str, Any] = {}
    if semantic_title and semantic_title != current_title:
        property_updates[title_prop] = title_property_payload(semantic_title)
    if aliases_prop and aliases_prop in props_meta:
        merged_aliases = merge_alias_values(existing_aliases, raw_title, semantic_title, current_title)
        if merged_aliases:
            property_updates[aliases_prop] = property_payload_for_value(props_meta[aliases_prop], merged_aliases)
    if topic_prop and topic_prop in props_meta:
        inferred_topics = infer_topics(semantic_title or current_title, note)
        if inferred_topics:
            property_updates[topic_prop] = property_payload_for_value(props_meta[topic_prop], inferred_topics)
    if compounded_level_prop and compounded_level_prop in props_meta:
        current_level = page.get("properties", {}).get(compounded_level_prop, {}).get("number")
        if current_level is None:
            property_updates[compounded_level_prop] = {"number": 1}
    if last_compounded_at_prop and last_compounded_at_prop in props_meta:
        property_updates[last_compounded_at_prop] = property_payload_for_value(props_meta[last_compounded_at_prop], today_iso_date())

    if property_updates:
        client.update_page(wiki_page_id, {"properties": property_updates})

    appended_structured_summary = False
    if "结构化整理" not in existing_body_text:
        client.append_block_children(
            wiki_page_id,
            build_structured_refinement_blocks(semantic_title or current_title, note, raw_title),
        )
        appended_structured_summary = True

    appended_deepening = False
    deepening_markers = ["核心判断", "实现信号", "关联概念", "与相邻概念的区别", "原文证据"]
    if any(marker not in existing_body_text for marker in deepening_markers):
        client.append_block_children(
            wiki_page_id,
            build_deepening_blocks(semantic_title or current_title, note),
        )
        appended_deepening = True

    return {
        "renamed_to": semantic_title if semantic_title != current_title else current_title,
        "property_updates": sorted(property_updates.keys()),
        "appended_structured_summary": appended_structured_summary,
        "appended_deepening": appended_deepening,
    }


def compile_raw_page(
    client: NotionClient,
    raw_database_id: str,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
    raw_page_id: str,
) -> Dict[str, Any]:
    raw_page = client.retrieve_page(raw_page_id)
    if normalize_notion_id(database_parent_id(raw_page)) != normalize_notion_id(raw_database_id):
        raise NotionError("Raw page does not belong to NOTION_RAW_INBOX_DB_ID")

    raw_database = client.retrieve_database(raw_database_id)
    raw_title_property = args.raw_title_property or mapping.get("raw_title_property") or detect_title_property(raw_database)
    if not raw_title_property:
        raise NotionError("Unable to determine raw title property")

    title = args.title or extract_title(raw_page, raw_title_property)
    if not title:
        raise NotionError("Raw page title is empty; pass --title explicitly")

    note = read_page_body_text(client, raw_page_id)
    if not note:
        raise NotionError("Raw page body is empty; nothing to compile")
    body_hash = sha256_text(note)

    last_compile = find_last_successful_compile(raw_page_id)
    if last_compile and not getattr(args, "force", False):
        if last_compile.get("body_hash") == body_hash:
            raw_props_meta = raw_database.get("properties", {})
            status_prop_name = args.raw_status_property or mapping.get("raw_status_property")
            compiled_status = args.raw_compiled_status or mapping.get("raw_compiled_status", "Compiled")
            skipped_raw_updates: Dict[str, Any] = {}
            if status_prop_name and status_prop_name in raw_props_meta:
                current_status = extract_property_text(raw_page, status_prop_name)
                if current_status != compiled_status:
                    skipped_raw_updates[status_prop_name] = property_payload_for_value(
                        raw_props_meta[status_prop_name], compiled_status
                    )
            if skipped_raw_updates:
                client.update_page(raw_page_id, {"properties": skipped_raw_updates})
            return {
                "timestamp": iso_now(),
                "action": "skipped_unchanged",
                "raw_page_id": raw_page_id,
                "raw_title": title,
                "body_hash": body_hash,
                "source_url": last_compile.get("source_url"),
                "wiki": last_compile.get("wiki"),
                "raw_updates": list(skipped_raw_updates.keys()),
                "reason": "body_hash_unchanged",
            }

    if not getattr(args, "force", False):
        cross_match = find_prior_compile_by_body_hash(body_hash, exclude_raw_page_id=raw_page_id)
        if cross_match:
            raw_props_meta = raw_database.get("properties", {})
            status_prop_name = args.raw_status_property or mapping.get("raw_status_property")
            compiled_status = args.raw_compiled_status or mapping.get("raw_compiled_status", "Compiled")
            skipped_raw_updates: Dict[str, Any] = {}
            if status_prop_name and status_prop_name in raw_props_meta:
                current_status = extract_property_text(raw_page, status_prop_name)
                if current_status != compiled_status:
                    skipped_raw_updates[status_prop_name] = property_payload_for_value(
                        raw_props_meta[status_prop_name], compiled_status
                    )
            if skipped_raw_updates:
                client.update_page(raw_page_id, {"properties": skipped_raw_updates})
            print(
                f"WARN: body_hash already compiled from raw {cross_match.get('raw_page_id')!r}; "
                "skipping to avoid duplicate write. Use --force to override.",
                file=sys.stderr,
            )
            return {
                "timestamp": iso_now(),
                "action": "skipped_duplicate_body",
                "raw_page_id": raw_page_id,
                "raw_title": title,
                "body_hash": body_hash,
                "wiki": cross_match.get("wiki"),
                "raw_updates": list(skipped_raw_updates.keys()),
                "originating_raw_page_id": cross_match.get("raw_page_id"),
                "reason": "body_hash_matches_different_raw",
            }

    source_prop_name = args.raw_source_url_property or mapping.get("raw_source_url_property")
    source_url = extract_property_text(raw_page, source_prop_name) if source_prop_name else None

    upsert_args = argparse.Namespace(
        title=title,
        note=note,
        source_url=source_url,
        canonical_id=args.canonical_id,
        verification=args.verification,
        compounded_level=args.compounded_level,
        last_compounded_at=args.last_compounded_at or today_iso_date(),
        append_heading=args.append_heading,
        increment_compounded_level=args.increment_compounded_level,
        title_property=args.title_property,
        canonical_id_property=args.canonical_id_property,
        verification_property=args.verification_property,
        compounded_level_property=args.compounded_level_property,
        last_compounded_at_property=args.last_compounded_at_property,
        strict_alias=getattr(args, "strict_alias", False),
        strict_fuzzy=getattr(args, "strict_fuzzy", False),
    )

    merge_mode = getattr(args, "merge_mode", "append")
    if merge_mode not in {"append", "propose", "replace"}:
        raise NotionError(f"--merge-mode must be append|propose|replace, got {merge_mode!r}")

    if merge_mode == "propose":
        wiki_database = client.retrieve_database(wiki_database_id)
        target = find_upsert_target(client, wiki_database, wiki_database_id, mapping, upsert_args)
        append_heading = upsert_args.append_heading or mapping.get("append_heading", "增量更新")
        proposed_blocks = build_append_blocks(note, append_heading, source_url)
        existing_body = ""
        existing_body_hash: Optional[str] = None
        if target["exact_match"]:
            existing_body = read_page_body_text(client, target["exact_match"]["id"])
            existing_body_hash = sha256_text(existing_body) if existing_body else None
        return {
            "timestamp": iso_now(),
            "action": "proposed",
            "raw_page_id": raw_page_id,
            "raw_title": title,
            "body_hash": body_hash,
            "body_text": truncate_body_text(note),
            "source_url": source_url,
            "target": {
                "exact_match_page_id": target["exact_match"]["id"] if target["exact_match"] else None,
                "match_strategy": target["match_strategy"],
                "fuzzy_candidate_ids": target["fuzzy_candidate_ids"],
                "existing_body_hash": existing_body_hash,
                "existing_body_excerpt": truncate_body_text(existing_body, 2000) if existing_body else "",
            },
            "proposed_append_block_count": len(proposed_blocks),
            "proposed_append_heading": append_heading,
            "raw_updates": [],
            "warnings": ["merge-mode=propose: no writes performed; rerun without --merge-mode=propose to commit"],
        }

    if merge_mode == "replace":
        replace_heading = getattr(args, "replace_heading", None)
        if not replace_heading:
            raise NotionError("--merge-mode=replace requires --replace-heading <heading_text>")
        wiki_database = client.retrieve_database(wiki_database_id)
        target = find_upsert_target(client, wiki_database, wiki_database_id, mapping, upsert_args)
        if not target["exact_match"]:
            raise NotionError("--merge-mode=replace requires an existing wiki page match; none found")
        wiki_page_id = target["exact_match"]["id"]
        top_blocks = iterate_block_children(client, wiki_page_id)
        heading_block, section_body = find_section_body(top_blocks, replace_heading)
        if heading_block is None:
            raise NotionError(
                f"Heading {replace_heading!r} not found on wiki page {wiki_page_id!r}"
            )
        deleted_ids: List[str] = []
        for b in section_body:
            bid = b.get("id")
            if not bid:
                continue
            try:
                client.delete_block(bid)
                deleted_ids.append(bid)
            except NotionError as exc:
                print(f"WARN: failed to delete block {bid}: {exc}", file=sys.stderr)
        new_blocks: List[Dict[str, Any]] = []
        for chunk in chunk_text(note):
            new_blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": rich_text_value(chunk)},
                }
            )
        if source_url:
            new_blocks.append(
                {
                    "object": "block",
                    "type": "bookmark",
                    "bookmark": {"url": source_url},
                }
            )
        client.append_block_children(heading_block["id"], new_blocks)

        raw_props_meta = raw_database.get("properties", {})
        raw_updates: Dict[str, Any] = {}
        status_prop_name = args.raw_status_property or mapping.get("raw_status_property")
        processed_at_prop_name = args.raw_processed_at_property or mapping.get("raw_processed_at_property")
        target_prop_name = args.raw_target_wiki_page_property or mapping.get("raw_target_wiki_page_property")
        compiled_status = args.raw_compiled_status or mapping.get("raw_compiled_status", "Compiled")
        if status_prop_name and status_prop_name in raw_props_meta:
            raw_updates[status_prop_name] = property_payload_for_value(raw_props_meta[status_prop_name], compiled_status)
        if processed_at_prop_name and processed_at_prop_name in raw_props_meta:
            raw_updates[processed_at_prop_name] = property_payload_for_value(raw_props_meta[processed_at_prop_name], today_iso_date())
        if target_prop_name and target_prop_name in raw_props_meta:
            target_meta = raw_props_meta[target_prop_name]
            if target_meta.get("type") == "relation" and relation_targets_database(target_meta, wiki_database_id):
                raw_updates[target_prop_name] = property_payload_for_value(target_meta, [wiki_page_id])
        if raw_updates:
            client.update_page(raw_page_id, {"properties": raw_updates})

        return {
            "timestamp": iso_now(),
            "action": "section_replaced",
            "raw_page_id": raw_page_id,
            "raw_title": title,
            "body_hash": body_hash,
            "body_text": truncate_body_text(note),
            "source_url": source_url,
            "wiki": {
                "page_id": wiki_page_id,
                "replaced_heading": replace_heading,
                "deleted_block_count": len(deleted_ids),
                "deleted_block_ids": deleted_ids,
                "new_block_count": len(new_blocks),
                "match_strategy": target["match_strategy"],
            },
            "raw_updates": list(raw_updates.keys()),
        }

    # Default: merge_mode == "append"
    wiki_result = upsert_note_to_wiki(client, wiki_database_id, mapping, upsert_args)

    raw_props_meta = raw_database.get("properties", {})
    raw_updates: Dict[str, Any] = {}
    warnings: List[str] = []
    status_prop_name = args.raw_status_property or mapping.get("raw_status_property")
    processed_at_prop_name = args.raw_processed_at_property or mapping.get("raw_processed_at_property")
    target_prop_name = args.raw_target_wiki_page_property or mapping.get("raw_target_wiki_page_property")
    compiled_status = args.raw_compiled_status or mapping.get("raw_compiled_status", "Compiled")

    if status_prop_name and status_prop_name in raw_props_meta:
        raw_updates[status_prop_name] = property_payload_for_value(raw_props_meta[status_prop_name], compiled_status)
    if processed_at_prop_name and processed_at_prop_name in raw_props_meta:
        raw_updates[processed_at_prop_name] = property_payload_for_value(raw_props_meta[processed_at_prop_name], today_iso_date())
    if target_prop_name and target_prop_name in raw_props_meta:
        target_meta = raw_props_meta[target_prop_name]
        if target_meta.get("type") == "relation" and not relation_targets_database(target_meta, wiki_database_id):
            warnings.append(
                f"{target_prop_name} relation points to database "
                f"{target_meta.get('relation', {}).get('database_id')} instead of wiki database {wiki_database_id}"
            )
        else:
            raw_updates[target_prop_name] = property_payload_for_value(target_meta, [wiki_result["page_id"]])
    if raw_updates:
        client.update_page(raw_page_id, {"properties": raw_updates})

    refinement = {
        "renamed_to": wiki_result["title"],
        "property_updates": [],
        "appended_structured_summary": False,
        "skipped": True,
    }
    if getattr(args, "auto_refine", False):
        refinement = apply_low_risk_refinement(
            client,
            wiki_database_id,
            mapping,
            wiki_result["page_id"],
            title,
            note,
            wiki_result["action"],
        )
        refinement["skipped"] = False

    diff_appended = False
    diff_summary: Optional[str] = None
    if getattr(args, "emit_diff", False) and wiki_result.get("action") == "updated":
        prev_body = last_compile.get("body_text") if last_compile else None
        if prev_body:
            diff_text = compute_paragraph_diff(prev_body, note)
            if diff_text.strip():
                diff_summary = diff_text
                diff_blocks: List[Dict[str, Any]] = [
                    {
                        "object": "block",
                        "type": "heading_3",
                        "heading_3": {"rich_text": rich_text_value(f"差异分析 {today_iso_date()}")},
                    }
                ]
                for chunk in chunk_text(diff_text):
                    diff_blocks.append(
                        {
                            "object": "block",
                            "type": "code",
                            "code": {"rich_text": rich_text_value(chunk), "language": "plain text"},
                        }
                    )
                client.append_block_children(wiki_result["page_id"], diff_blocks)
                diff_appended = True
        else:
            warnings.append("--emit-diff skipped: no previous body_text in compile-log")

    return {
        "timestamp": iso_now(),
        "action": "compiled",
        "raw_page_id": raw_page_id,
        "raw_title": title,
        "body_hash": body_hash,
        "body_text": truncate_body_text(note),
        "wiki": wiki_result,
        "raw_updates": list(raw_updates.keys()),
        "source_url": source_url,
        "low_risk_refinement": refinement,
        "warnings": warnings,
        "diff_appended": diff_appended,
        "diff_summary": diff_summary[:400] if diff_summary else None,
    }


def inspect_schema(client: NotionClient, database_id: str, database_role: str) -> int:
    database = client.retrieve_database(database_id)
    title_prop = detect_title_property(database)
    payload = {
        "database_role": database_role,
        "database_id": database.get("id"),
        "title_property": title_prop,
        "properties": {name: meta.get("type") for name, meta in database.get("properties", {}).items()},
    }
    snapshot_path = write_json_snapshot(
        f"{timestamp_slug()}-inspect-schema-{database_role}.json",
        payload,
    )
    payload["snapshot_path"] = str(snapshot_path)
    print(json.dumps(audit_success("inspect-schema", payload), ensure_ascii=False, indent=2))
    return 0


def command_search(client: NotionClient, database_id: str, mapping: Dict[str, Any], args: argparse.Namespace) -> int:
    database = client.retrieve_database(database_id)
    title_prop = resolve_title_property_name(database, mapping, args.title_property)
    title_prop_type = database.get("properties", {}).get(title_prop, {}).get("type", "title")
    aliases_prop = mapping.get("aliases_property")
    aliases_prop_type = None
    if aliases_prop not in database.get("properties", {}):
        aliases_prop = None
    else:
        aliases_prop_type = database.get("properties", {}).get(aliases_prop, {}).get("type")
    results = search_in_database(
        client,
        database_id,
        args.query,
        title_prop,
        title_prop_type,
        aliases_prop,
        aliases_prop_type,
    )
    summary = [
        {
            "page_id": page.get("id"),
            "title": extract_title(page, title_prop),
            "last_edited_time": page.get("last_edited_time"),
            "url": page.get("url"),
        }
        for page in results
    ]
    print(
        json.dumps(
            audit_success(
                "search",
                {
                    "query": args.query,
                    "database_id": database_id,
                    "result_count": len(summary),
                    "results": summary,
                },
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_upsert(client: NotionClient, database_id: str, mapping: Dict[str, Any], args: argparse.Namespace) -> int:
    result = upsert_note_to_wiki(client, database_id, mapping, args)
    print(
        json.dumps(
            audit_success(
                "upsert-note",
                {
                    "database_id": database_id,
                    "title": args.title,
                    "wiki": result,
                },
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_compile_from_raw(
    client: NotionClient,
    raw_database_id: str,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    payload = compile_raw_page(client, raw_database_id, wiki_database_id, mapping, args, args.page_id)
    print(json.dumps(finalize_compile_payload(payload), ensure_ascii=False, indent=2))
    return 0


def command_compile_queue(
    client: NotionClient,
    raw_database_id: str,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    raw_database = client.retrieve_database(raw_database_id)
    raw_page_ids: List[str] = []
    filter_description: Dict[str, Any] = {}

    if getattr(args, "retry_failed", False):
        raw_page_ids = find_last_compile_queue_failures()
        filter_description = {"mode": "retry_failed", "source": "last compile-queue audit entry"}
        if not raw_page_ids:
            print(
                json.dumps(
                    audit_success(
                        "compile-queue",
                        {
                            **filter_description,
                            "compiled_count": 0,
                            "failure_count": 0,
                            "results": [],
                            "failures": [],
                            "note": "no prior failures found in audit-log",
                        },
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    else:
        status_prop_name = args.raw_status_property or mapping.get("raw_status_property")
        raw_props_meta = raw_database.get("properties", {})
        status_prop_meta = raw_props_meta.get(status_prop_name, {})
        status_prop_type = status_prop_meta.get("type")
        if not status_prop_name or not status_prop_type:
            raise NotionError("Raw status property not found; pass --raw-status-property or update mapping")

        and_filters: List[Dict[str, Any]] = [
            {"property": status_prop_name, status_prop_type: {"equals": args.status}}
        ]
        extra_filter_desc: List[Dict[str, str]] = []
        skipped_filters: List[Dict[str, str]] = []
        for raw_expr in getattr(args, "filter", []) or []:
            if "=" not in raw_expr:
                raise NotionError(f"--filter must be PROP=VALUE, got {raw_expr!r}")
            prop_name, raw_val = raw_expr.split("=", 1)
            prop_name = prop_name.strip()
            raw_val = raw_val.strip()
            prop_meta = raw_props_meta.get(prop_name)
            if not prop_meta:
                skipped_filters.append({"property": prop_name, "reason": "property not found in raw schema"})
                continue
            ptype = prop_meta.get("type")
            filter_fragment: Optional[Dict[str, Any]] = None
            if ptype in {"status", "select"}:
                filter_fragment = {"property": prop_name, ptype: {"equals": raw_val}}
            elif ptype in {"rich_text", "title"}:
                filter_fragment = {"property": prop_name, ptype: {"equals": raw_val}}
            elif ptype == "checkbox":
                filter_fragment = {"property": prop_name, "checkbox": {"equals": raw_val.lower() in {"true", "1", "yes"}}}
            elif ptype == "number":
                try:
                    num = float(raw_val)
                except ValueError as exc:
                    raise NotionError(f"--filter {prop_name}={raw_val}: value is not a number") from exc
                filter_fragment = {"property": prop_name, "number": {"equals": num}}
            elif ptype == "multi_select":
                filter_fragment = {"property": prop_name, "multi_select": {"contains": raw_val}}
            else:
                skipped_filters.append({"property": prop_name, "reason": f"unsupported type {ptype!r}"})
                continue
            and_filters.append(filter_fragment)
            extra_filter_desc.append({"property": prop_name, "value": raw_val})

        filter_body: Dict[str, Any] = and_filters[0] if len(and_filters) == 1 else {"and": and_filters}
        raw_pages = query_database_pages(client, raw_database_id, filter_body, page_size=min(args.limit, 20) if args.limit else 20)
        if args.limit:
            raw_pages = raw_pages[: args.limit]
        raw_page_ids = [page.get("id") for page in raw_pages if page.get("id")]
        filter_description = {
            "status_filter": args.status,
            "requested_limit": args.limit,
            "extra_filters": extra_filter_desc,
        }
        if skipped_filters:
            filter_description["skipped_filters"] = skipped_filters
            for s in skipped_filters:
                print(
                    f"WARN: --filter {s['property']!r} skipped: {s['reason']}",
                    file=sys.stderr,
                )

    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for page_id in raw_page_ids:
        if not page_id:
            continue
        try:
            payload = compile_raw_page(client, raw_database_id, wiki_database_id, mapping, args, page_id)
            results.append(finalize_compile_payload(payload))
        except Exception as exc:
            failures.append({"raw_page_id": page_id, "error": str(exc)})

    print(
        json.dumps(
            audit_success(
                "compile-queue",
                {
                    **filter_description,
                    "compiled_count": len(results),
                    "failure_count": len(failures),
                    "results": results,
                    "failures": failures,
                },
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not failures else 1


def command_lint(client: NotionClient, database_id: str, mapping: Dict[str, Any], args: argparse.Namespace) -> int:
    database = client.retrieve_database(database_id)
    props_meta = database.get("properties", {})
    title_prop = args.title_property or mapping.get("title_property") or detect_title_property(database)
    verification_prop = args.verification_property or mapping.get("verification_property")
    if not title_prop:
        raise NotionError("Unable to determine title property")
    if not verification_prop or verification_prop not in props_meta:
        raise NotionError("Verification property not found; pass --verification-property or update mapping")

    expired_values = args.expired_values or mapping.get("expired_status_values") or ["Expired"]
    hits: List[Dict[str, Any]] = []
    for value in expired_values:
        filter_body = {
            "property": verification_prop,
            props_meta[verification_prop]["type"]: {"equals": value},
        }
        results = query_database_pages(client, database_id, filter_body)
        for page in results:
            hits.append(
                {
                    "page_id": page.get("id"),
                    "title": extract_title(page, title_prop),
                    "verification": value,
                    "last_edited_time": page.get("last_edited_time"),
                    "url": page.get("url"),
                }
            )
    print(
        json.dumps(
            audit_success(
                "lint",
                {
                    "database_id": database_id,
                    "expired_values": expired_values,
                    "result_count": len(hits),
                    "results": hits,
                },
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_log_session_event(args: argparse.Namespace) -> int:
    allowed_tiers = {"canonical_id", "title", "alias", "fuzzy", "none"}
    allowed_decisions = {"update", "create", "ask_user", "skip"}
    allowed_risks = {"low", "medium", "high"}
    if args.tier not in allowed_tiers:
        raise NotionError(f"--tier must be one of {sorted(allowed_tiers)}")
    if args.decision not in allowed_decisions:
        raise NotionError(f"--decision must be one of {sorted(allowed_decisions)}")
    if args.risk not in allowed_risks:
        raise NotionError(f"--risk must be one of {sorted(allowed_risks)}")

    event: Dict[str, Any] = {
        "timestamp": iso_now(),
        "model": args.model,
        "raw_page_id": args.raw_page_id,
        "wiki_page_id": args.wiki_page_id,
        "tier": args.tier,
        "decision": args.decision,
        "risk": args.risk,
        "notes": args.notes or "",
    }
    if args.input_json:
        try:
            event["input"] = json.loads(args.input_json)
        except json.JSONDecodeError as exc:
            raise NotionError(f"--input-json is not valid JSON: {exc}") from exc

    log_path = append_jsonl_log(daily_log_filename("session-log.jsonl"), event)
    event["log_path"] = str(log_path)
    print(json.dumps(audit_success("log-session-event", event), ensure_ascii=False, indent=2))
    return 0


REQUIRED_EDITORIAL_HEADINGS = ("定义", "核心判断", "关联概念", "原文证据")


def count_update_section_duplicates(blocks: List[Dict[str, Any]], heading_prefix: str = "增量更新") -> int:
    keys: List[str] = []
    current_key_parts: Optional[List[str]] = None
    for block in blocks:
        text = ""
        if block.get("type") == "heading_2":
            text = rich_text_plain_text(block.get("heading_2", {}).get("rich_text", []))
        if text.startswith(heading_prefix):
            if current_key_parts is not None:
                key = "\n".join(p for p in current_key_parts if p).strip()
                if key:
                    keys.append(key)
            current_key_parts = []
        elif current_key_parts is not None:
            current_key_parts.append(extract_block_text(block))
    if current_key_parts is not None:
        key = "\n".join(p for p in current_key_parts if p).strip()
        if key:
            keys.append(key)
    seen: Dict[str, int] = {}
    dup = 0
    for key in keys:
        seen[key] = seen.get(key, 0) + 1
    for count in seen.values():
        if count > 1:
            dup += count - 1
    return dup


def count_evidence_items(blocks: List[Dict[str, Any]]) -> int:
    in_section = False
    count = 0
    for block in blocks:
        btype = block.get("type")
        if btype in ("heading_2", "heading_3"):
            text = rich_text_plain_text(block.get(btype, {}).get("rich_text", []))
            in_section = text.startswith("原文证据")
            continue
        if in_section and btype in ("quote", "bulleted_list_item", "paragraph"):
            if extract_block_text(block).strip():
                count += 1
    return count


def check_editorial_compliance(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    wiki_page_id: str,
) -> Dict[str, Any]:
    database = client.retrieve_database(wiki_database_id)
    props_meta = database.get("properties", {})
    page = client.retrieve_page(wiki_page_id)
    title_prop = mapping.get("title_property") or detect_title_property(database)
    title = extract_title(page, title_prop) if title_prop else ""

    body_text = read_page_body_text(client, wiki_page_id)
    if is_placeholder_page(body_text):
        return {
            "wiki_page_id": wiki_page_id,
            "title": title,
            "compliance": "placeholder",
            "issue_count": 0,
            "issues": [],
            "note": "page body starts with <placeholder> marker; created by seed-related-pages, awaiting session-layer editorial",
        }

    issues: List[Dict[str, Any]] = []

    required_prop_keys = [
        mapping.get("canonical_id_property"),
        mapping.get("verification_property"),
        mapping.get("compounded_level_property"),
        mapping.get("last_compounded_at_property"),
    ]
    for prop_name in required_prop_keys:
        if not prop_name:
            continue
        if prop_name not in props_meta:
            issues.append({"check": "required_property_missing_in_schema", "property": prop_name})
            continue
        value = extract_property_text(page, prop_name)
        if not value:
            issues.append({"check": "required_property_empty", "property": prop_name})

    if re.match(r"^第\s*\d+\s*章", title):
        issues.append({"check": "title_not_normalized", "hint": "strip 第N章 prefix"})
    if "：" in title or ":" in title:
        issues.append({"check": "title_contains_delimiter", "hint": "consider splitting before ：/:"})

    blocks = iterate_block_children(client, wiki_page_id)
    heading_texts = conceptual_heading_set(blocks)
    for required in REQUIRED_EDITORIAL_HEADINGS:
        if not any(required in h for h in heading_texts):
            issues.append({"check": "missing_heading", "heading": required})

    evidence_count = count_evidence_items(blocks)
    if evidence_count > 4:
        issues.append({"check": "too_many_evidence_items", "count": evidence_count, "hint": "cap at 4 per EDITORIAL_POLICY"})

    dup_count = count_update_section_duplicates(blocks)
    if dup_count > 0:
        issues.append({"check": "duplicate_update_sections", "count": dup_count, "hint": "run cleanup-wiki-page"})

    if not issues:
        compliance = "green"
    elif len(issues) <= 2:
        compliance = "yellow"
    else:
        compliance = "red"

    return {
        "wiki_page_id": wiki_page_id,
        "title": title,
        "compliance": compliance,
        "issue_count": len(issues),
        "issues": issues,
    }


def command_consolidate_evidence(client: NotionClient, args: argparse.Namespace) -> int:
    page_id = args.page_id
    keep = max(1, int(args.keep))
    heading_text = args.heading or "原文证据"
    top_blocks = iterate_block_children(client, page_id)
    heading_block, section_body = find_section_body(top_blocks, heading_text)
    if heading_block is None:
        payload = {
            "wiki_page_id": page_id,
            "heading": heading_text,
            "action": "skipped",
            "reason": "heading_not_found",
        }
        print(json.dumps(audit_success("consolidate-evidence", payload), ensure_ascii=False, indent=2))
        return 0

    kept_block_ids: List[str] = []
    removed_block_ids: List[str] = []
    evidence_seen = 0
    for block in section_body:
        btype = block.get("type")
        if btype in ("quote", "bulleted_list_item", "paragraph"):
            if not extract_block_text(block).strip():
                continue
            evidence_seen += 1
            bid = block.get("id")
            if not bid:
                continue
            if evidence_seen <= keep:
                kept_block_ids.append(bid)
            else:
                if getattr(args, "dry_run", False):
                    removed_block_ids.append(bid)
                    continue
                try:
                    client.delete_block(bid)
                    removed_block_ids.append(bid)
                except NotionError as exc:
                    print(f"WARN: failed to delete block {bid}: {exc}", file=sys.stderr)

    payload = {
        "wiki_page_id": page_id,
        "heading": heading_text,
        "keep": keep,
        "evidence_seen": evidence_seen,
        "kept_block_ids": kept_block_ids,
        "removed_block_ids": removed_block_ids,
        "dry_run": getattr(args, "dry_run", False),
    }
    print(json.dumps(audit_success("consolidate-evidence", payload), ensure_ascii=False, indent=2))
    return 0


def command_check_editorial(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    if getattr(args, "all", False):
        pages = query_database_pages(client, wiki_database_id, None, page_size=20, max_pages=5)
        pages = pages[: args.limit] if args.limit else pages
        results = [
            check_editorial_compliance(client, wiki_database_id, mapping, page.get("id"))
            for page in pages
            if page.get("id")
        ]
        summary = {
            "wiki_database_id": wiki_database_id,
            "scope": "all",
            "checked_count": len(results),
            "green": sum(1 for r in results if r["compliance"] == "green"),
            "yellow": sum(1 for r in results if r["compliance"] == "yellow"),
            "red": sum(1 for r in results if r["compliance"] == "red"),
            "results": results,
        }
        print(json.dumps(audit_success("check-editorial", summary), ensure_ascii=False, indent=2))
        return 0 if all(r["compliance"] == "green" for r in results) else 1
    if not args.page_id:
        raise NotionError("check-editorial requires either <page_id> or --all")
    result = check_editorial_compliance(client, wiki_database_id, mapping, args.page_id)
    print(json.dumps(audit_success("check-editorial", result), ensure_ascii=False, indent=2))
    return 0 if result["compliance"] == "green" else 1


def ensure_related_pages_property(
    client: NotionClient,
    wiki_database_id: str,
    prop_name: str = "Related Pages",
) -> Dict[str, Any]:
    db = client.retrieve_database(wiki_database_id)
    props = db.get("properties", {})
    existing = props.get(prop_name)
    if existing and existing.get("type") == "relation":
        rel = existing.get("relation", {})
        if normalize_notion_id(rel.get("database_id")) == normalize_notion_id(wiki_database_id):
            return {"action": "already_exists", "property": prop_name}
    payload = {
        "properties": {
            prop_name: {
                "relation": {
                    "database_id": wiki_database_id,
                    "type": "single_property",
                    "single_property": {},
                }
            }
        }
    }
    client.update_database(wiki_database_id, payload)
    return {"action": "created", "property": prop_name}


def parse_mention_map(raw: Optional[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not raw:
        return mapping
    for item in raw.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        label, pid = item.split("=", 1)
        label = label.strip()
        pid = pid.strip()
        if label and pid:
            mapping[label] = pid
    return mapping


def build_rich_text_with_mentions(
    text: str,
    mention_map: Dict[str, str],
    link_style: str = "mention",
) -> List[Dict[str, Any]]:
    """Build rich_text segments with inline page references.

    link_style='mention': use Notion mention type (preferred semantically, but
        UI may under-render when created via API);
    link_style='link': use text with href link pointing at notion.so URL
        (renders as blue underlined clickable text, robust across clients);
    link_style='both': emit mention + immediately a text-link with the same
        label, belt-and-suspenders if mention renders empty.
    """
    if not mention_map:
        return rich_text_value(text)
    labels = sorted(mention_map.keys(), key=len, reverse=True)
    pattern = "|".join(re.escape(l) for l in labels)
    segments: List[Dict[str, Any]] = []
    pos = 0
    for match in re.finditer(pattern, text):
        if match.start() > pos:
            segments.append({"type": "text", "text": {"content": text[pos:match.start()]}})
        label = match.group(0)
        target_id = mention_map[label]
        if link_style in ("mention", "both"):
            segments.append(
                {
                    "type": "mention",
                    "mention": {"type": "page", "page": {"id": target_id}},
                }
            )
        if link_style in ("link", "both"):
            url = f"https://www.notion.so/{normalize_notion_id(target_id)}"
            segments.append(
                {
                    "type": "text",
                    "text": {"content": label, "link": {"url": url}},
                }
            )
        pos = match.end()
    if pos < len(text):
        segments.append({"type": "text", "text": {"content": text[pos:]}})
    return segments or rich_text_value(text)


def command_link_concepts_in_page(client: NotionClient, args: argparse.Namespace) -> int:
    mention_map = parse_mention_map(args.mention_map)
    if not mention_map:
        raise NotionError("--mention-map is required (LABEL=page_id,...)")
    link_style = getattr(args, "link_style", "link")
    linkable_block_types = {"paragraph", "heading_2", "heading_3", "quote", "bulleted_list_item", "numbered_list_item"}
    top_blocks = iterate_block_children(client, args.page_id)
    touched: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for block in top_blocks:
        btype = block.get("type")
        if btype not in linkable_block_types:
            continue
        rt = block.get(btype, {}).get("rich_text", []) or []
        plain = rich_text_plain_text(rt)
        if not plain:
            continue
        if not any(label in plain for label in mention_map):
            continue
        has_existing_link = any(
            seg.get("type") == "mention"
            or (seg.get("type") == "text" and seg.get("text", {}).get("link"))
            for seg in rt
        )
        if has_existing_link and not getattr(args, "force", False):
            skipped.append({"block_id": block.get("id"), "reason": "already_has_link", "preview": plain[:60]})
            continue
        new_rt = build_rich_text_with_mentions(plain, mention_map, link_style)
        bid = block.get("id")
        if not bid:
            continue
        if getattr(args, "dry_run", False):
            touched.append({"block_id": bid, "type": btype, "preview": plain[:60], "dry_run": True})
            continue
        try:
            client.update_block(bid, {btype: {"rich_text": new_rt}})
            touched.append({"block_id": bid, "type": btype, "preview": plain[:60]})
        except NotionError as exc:
            skipped.append({"block_id": bid, "reason": str(exc)})
    payload = {
        "wiki_page_id": args.page_id,
        "link_style": link_style,
        "labels": sorted(mention_map.keys()),
        "touched_count": len(touched),
        "skipped_count": len(skipped),
        "touched": touched,
        "skipped": skipped,
        "dry_run": getattr(args, "dry_run", False),
    }
    print(json.dumps(audit_success("link-concepts-in-page", payload), ensure_ascii=False, indent=2))
    return 0


def command_link_pages(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    rel_prop = mapping.get("related_pages_property")
    if not rel_prop:
        raise NotionError("mapping missing 'related_pages_property'; update schema/notion_wiki_mapping.example.json")
    if getattr(args, "ensure_property", False):
        ensure_related_pages_property(client, wiki_database_id, rel_prop)
    database = client.retrieve_database(wiki_database_id)
    if rel_prop not in database.get("properties", {}):
        raise NotionError(
            f"Wiki database has no {rel_prop!r} property; rerun with --ensure-property or add it in Notion"
        )
    page = client.retrieve_page(args.page_id)
    existing = page.get("properties", {}).get(rel_prop, {}).get("relation", []) or []
    existing_ids = [v.get("id") for v in existing if v.get("id")]
    existing_norm = {normalize_notion_id(pid): pid for pid in existing_ids}
    add_ids_raw = args.add or []
    remove_ids_raw = args.remove or []
    remove_norm = {normalize_notion_id(pid) for pid in remove_ids_raw}
    for pid in add_ids_raw:
        existing_norm.setdefault(normalize_notion_id(pid), pid)
    for key in list(existing_norm.keys()):
        if key in remove_norm:
            existing_norm.pop(key, None)
    new_ids = list(existing_norm.values())
    if not getattr(args, "dry_run", False):
        client.update_page(
            args.page_id,
            {"properties": {rel_prop: {"relation": [{"id": pid} for pid in new_ids]}}},
        )
    payload = {
        "wiki_page_id": args.page_id,
        "property": rel_prop,
        "previous_related_count": len(existing_ids),
        "new_related_count": len(new_ids),
        "new_related_ids": new_ids,
        "dry_run": getattr(args, "dry_run", False),
    }
    print(json.dumps(audit_success("link-pages", payload), ensure_ascii=False, indent=2))
    return 0


def command_rewrite_section(client: NotionClient, args: argparse.Namespace) -> int:
    page_id = args.page_id
    heading = args.heading
    body = args.body
    if not body.strip():
        raise NotionError("--body must be non-empty text")

    top_blocks = iterate_block_children(client, page_id)
    heading_block, section_body = find_section_body(top_blocks, heading)
    if heading_block is None:
        raise NotionError(f"Heading {heading!r} not found on page {page_id!r}")

    deleted_ids: List[str] = []
    dry_run = getattr(args, "dry_run", False)
    for b in section_body:
        bid = b.get("id")
        if not bid:
            continue
        if dry_run:
            deleted_ids.append(bid)
            continue
        try:
            client.delete_block(bid)
            deleted_ids.append(bid)
        except NotionError as exc:
            print(f"WARN: failed to delete block {bid}: {exc}", file=sys.stderr)

    mention_map = parse_mention_map(getattr(args, "mention_map", None))
    link_style = getattr(args, "link_style", "link")
    new_blocks: List[Dict[str, Any]] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        paragraphs = [body]
    for para in paragraphs:
        for chunk in chunk_text(para):
            rich = (
                build_rich_text_with_mentions(chunk, mention_map, link_style)
                if mention_map
                else rich_text_value(chunk)
            )
            new_blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": rich},
                }
            )
    if not dry_run:
        client.append_block_children(page_id, new_blocks, after=heading_block["id"])

    promoted_block_id: Optional[str] = None
    if getattr(args, "promote", False):
        for b in top_blocks:
            if b.get("type") == "paragraph":
                text = extract_block_text(b).strip()
                if text.startswith(PLACEHOLDER_MARKER):
                    bid = b.get("id")
                    if not bid:
                        break
                    if dry_run:
                        promoted_block_id = bid
                    else:
                        try:
                            client.delete_block(bid)
                            promoted_block_id = bid
                        except NotionError as exc:
                            print(f"WARN: failed to delete placeholder marker: {exc}", file=sys.stderr)
                    break

    payload = {
        "wiki_page_id": page_id,
        "heading": heading,
        "deleted_block_count": len(deleted_ids),
        "deleted_block_ids": deleted_ids,
        "new_block_count": len(new_blocks),
        "promoted_from_placeholder": bool(promoted_block_id),
        "removed_placeholder_block_id": promoted_block_id,
        "dry_run": dry_run,
    }
    print(json.dumps(audit_success("rewrite-section", payload), ensure_ascii=False, indent=2))
    return 0


def compare_page_to_reference(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    reference_page_id: str,
    target_page_id: str,
) -> Dict[str, Any]:
    database = client.retrieve_database(wiki_database_id)
    props_meta = database.get("properties", {})
    title_prop = mapping.get("title_property") or detect_title_property(database)

    def fetch_profile(page_id: str) -> Dict[str, Any]:
        page = client.retrieve_page(page_id)
        blocks = iterate_block_children(client, page_id)
        headings = extract_heading_structure(blocks)
        conceptual = conceptual_heading_set(blocks)
        evidence_count = count_evidence_items(blocks)
        present_props = []
        for candidate_key in (
            "canonical_id_property",
            "verification_property",
            "compounded_level_property",
            "last_compounded_at_property",
            "aliases_property",
            "topic_property",
            "source_property",
        ):
            prop_name = mapping.get(candidate_key)
            if prop_name and prop_name in props_meta:
                value = extract_property_text(page, prop_name)
                if value:
                    present_props.append(prop_name)
        body_text = "\n\n".join(extract_block_text(b) for b in blocks if extract_block_text(b))
        return {
            "page_id": page_id,
            "title": extract_title(page, title_prop) if title_prop else "",
            "conceptual_headings": conceptual,
            "headings_all": headings,
            "evidence_count": evidence_count,
            "properties_filled": present_props,
            "is_placeholder": is_placeholder_page(body_text),
        }

    reference_profile = fetch_profile(reference_page_id)
    target_profile = fetch_profile(target_page_id)

    if target_profile["is_placeholder"]:
        return {
            "reference_page_id": reference_page_id,
            "reference_title": reference_profile["title"],
            "target_page_id": target_page_id,
            "target_title": target_profile["title"],
            "conformance": "placeholder",
            "issue_count": 0,
            "issues": [],
            "note": "target is a placeholder page; exempt from reference comparison until session-layer editorial",
        }

    ref_heading_set = {normalize(h): h for h in reference_profile["conceptual_headings"]}
    target_heading_set = {normalize(h): h for h in target_profile["conceptual_headings"]}
    missing_headings = sorted(ref_heading_set[k] for k in ref_heading_set.keys() - target_heading_set.keys())
    extra_headings = sorted(target_heading_set[k] for k in target_heading_set.keys() - ref_heading_set.keys())

    ref_props = set(reference_profile["properties_filled"])
    target_props = set(target_profile["properties_filled"])
    missing_properties = sorted(ref_props - target_props)

    issues: List[Dict[str, Any]] = []
    for h in missing_headings:
        issues.append({"check": "missing_heading_vs_reference", "heading": h})
    for p in missing_properties:
        issues.append({"check": "missing_property_vs_reference", "property": p})
    if target_profile["evidence_count"] > reference_profile["evidence_count"] and reference_profile["evidence_count"] > 0:
        issues.append({
            "check": "evidence_count_exceeds_reference",
            "reference_count": reference_profile["evidence_count"],
            "target_count": target_profile["evidence_count"],
        })
    if target_profile["is_placeholder"]:
        issues.append({"check": "target_is_placeholder", "hint": "target has <placeholder> marker; refine before conformance check"})

    if not issues:
        conformance = "green"
    elif len(issues) <= 2:
        conformance = "yellow"
    else:
        conformance = "red"

    return {
        "reference_page_id": reference_page_id,
        "reference_title": reference_profile["title"],
        "target_page_id": target_page_id,
        "target_title": target_profile["title"],
        "conformance": conformance,
        "missing_headings_vs_reference": missing_headings,
        "extra_headings_vs_reference": extra_headings,
        "missing_properties_vs_reference": missing_properties,
        "reference_evidence_count": reference_profile["evidence_count"],
        "target_evidence_count": target_profile["evidence_count"],
        "issue_count": len(issues),
        "issues": issues,
    }


def command_reference_check(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    if not args.reference_page_id:
        raise NotionError("reference-check requires a reference page id")
    if args.all:
        pages = query_database_pages(client, wiki_database_id, None, page_size=20, max_pages=5)
        pages = pages[: args.limit] if args.limit else pages
        results: List[Dict[str, Any]] = []
        for page in pages:
            pid = page.get("id")
            if not pid or normalize_notion_id(pid) == normalize_notion_id(args.reference_page_id):
                continue
            results.append(
                compare_page_to_reference(client, wiki_database_id, mapping, args.reference_page_id, pid)
            )
        summary = {
            "reference_page_id": args.reference_page_id,
            "scope": "all",
            "checked_count": len(results),
            "green": sum(1 for r in results if r["conformance"] == "green"),
            "yellow": sum(1 for r in results if r["conformance"] == "yellow"),
            "red": sum(1 for r in results if r["conformance"] == "red"),
            "results": results,
        }
        print(json.dumps(audit_success("reference-check", summary), ensure_ascii=False, indent=2))
        return 0 if all(r["conformance"] == "green" for r in results) else 1
    if not args.target_page_id:
        raise NotionError("reference-check requires <target_page_id> or --all")
    result = compare_page_to_reference(client, wiki_database_id, mapping, args.reference_page_id, args.target_page_id)
    print(json.dumps(audit_success("reference-check", result), ensure_ascii=False, indent=2))
    return 0 if result["conformance"] == "green" else 1


def build_placeholder_blocks(concept_label: str, source_title: str, source_page_id: str) -> List[Dict[str, Any]]:
    marker_rich_text: List[Dict[str, Any]] = [
        {"type": "text", "text": {"content": f"{PLACEHOLDER_MARKER} 此页面由 seed-related-pages 从源页 "}},
        {"type": "mention", "mention": {"type": "page", "page": {"id": source_page_id}}},
        {"type": "text", "text": {"content": " 自动创建，等会话层精修为真实永久笔记。"}},
    ]
    related_rich_text: List[Dict[str, Any]] = [
        {"type": "text", "text": {"content": "源页："}},
        {"type": "mention", "mention": {"type": "page", "page": {"id": source_page_id}}},
        {"type": "text", "text": {"content": "。其他关联由会话层补充。"}},
    ]
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": marker_rich_text},
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": rich_text_value("定义")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text_value(f"TBD：需会话层填入 {concept_label} 的定义。")},
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": rich_text_value("核心判断")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text_value("TBD：需会话层填入。")},
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": rich_text_value("关联概念")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": related_rich_text},
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": rich_text_value("原文证据")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text_value("TBD：需会话层从源页或其他资料摘取 ≤ 4 条高价值引文。")},
        },
    ]


def command_seed_related_pages(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    source_page_id = args.source_page_id
    database = client.retrieve_database(wiki_database_id)
    props_meta = database.get("properties", {})
    title_prop = mapping.get("title_property") or detect_title_property(database)
    title_prop_type = props_meta.get(title_prop, {}).get("type", "title") if title_prop else "title"
    aliases_prop = mapping.get("aliases_property")
    aliases_prop_type = props_meta.get(aliases_prop, {}).get("type") if aliases_prop and aliases_prop in props_meta else None
    verification_prop = mapping.get("verification_property")

    source_page = client.retrieve_page(source_page_id)
    source_title = extract_title(source_page, title_prop) if title_prop else ""
    if not source_title:
        raise NotionError(f"source page {source_page_id!r} has no title under property {title_prop!r}")
    source_body = read_page_body_text(client, source_page_id)
    inferred_concepts = infer_related_concepts(source_title, source_body)

    existing: List[Dict[str, Any]] = []
    created: List[Dict[str, Any]] = []
    skipped_as_source: List[str] = []
    for concept in inferred_concepts:
        if normalize(concept) == normalize(source_title):
            skipped_as_source.append(concept)
            continue
        candidates = search_in_database(
            client,
            wiki_database_id,
            concept,
            title_prop,
            title_prop_type,
            aliases_prop,
            aliases_prop_type,
        )
        exact_hit: Optional[Dict[str, Any]] = None
        for page in candidates:
            if normalize(extract_title(page, title_prop)) == normalize(concept):
                exact_hit = page
                break
        if exact_hit:
            existing.append({
                "concept": concept,
                "page_id": exact_hit.get("id"),
                "title": extract_title(exact_hit, title_prop),
            })
            continue
        if getattr(args, "dry_run", False):
            created.append({"concept": concept, "page_id": None, "dry_run": True})
            continue
        props: Dict[str, Any] = {title_prop: title_property_payload(concept)}
        if verification_prop and verification_prop in props_meta:
            vmeta = props_meta[verification_prop]
            chosen_status = None
            if vmeta.get("type") == "status":
                existing_options = [
                    opt.get("name")
                    for opt in vmeta.get("status", {}).get("options", []) or []
                    if opt.get("name")
                ]
                for candidate in ("Needs Review", "需要复核", "Unverified", "Pending", "To Review", "Draft", "In progress", "In Progress"):
                    if candidate in existing_options:
                        chosen_status = candidate
                        break
            elif vmeta.get("type") in ("select", "rich_text"):
                chosen_status = "Needs Review"
            if chosen_status:
                try:
                    props[verification_prop] = property_payload_for_value(vmeta, chosen_status)
                except NotionError:
                    pass
        related_prop = mapping.get("related_pages_property")
        if related_prop and related_prop in props_meta:
            rel_meta = props_meta[related_prop]
            if rel_meta.get("type") == "relation":
                props[related_prop] = {"relation": [{"id": source_page_id}]}
        new_page = client.create_page({
            "parent": {"database_id": wiki_database_id},
            "properties": props,
            "children": build_placeholder_blocks(concept, source_title, source_page_id),
        })
        created.append({"concept": concept, "page_id": new_page.get("id"), "title": concept})

    payload = {
        "source_page_id": source_page_id,
        "source_title": source_title,
        "dry_run": getattr(args, "dry_run", False),
        "inferred_concept_count": len(inferred_concepts),
        "inferred_concepts": inferred_concepts,
        "existing_concept_pages": existing,
        "created_placeholder_pages": created,
        "skipped_self_reference": skipped_as_source,
    }
    print(json.dumps(audit_success("seed-related-pages", payload), ensure_ascii=False, indent=2))
    return 0


def command_cleanup_wiki_page(client: NotionClient, args: argparse.Namespace) -> int:
    page_id = args.page_id
    append_heading_prefix = args.heading_prefix or "增量更新"
    blocks = iterate_block_children(client, page_id)

    sections: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    current: Optional[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = None
    for block in blocks:
        heading_text = ""
        if block.get("type") == "heading_2":
            heading_text = rich_text_plain_text(block.get("heading_2", {}).get("rich_text", []))
        if heading_text.startswith(append_heading_prefix):
            if current is not None:
                sections.append(current)
            current = (block, [])
        elif current is not None:
            current[1].append(block)
    if current is not None:
        sections.append(current)

    seen: Dict[str, int] = {}
    to_delete: List[Dict[str, Any]] = []
    kept_indices: List[int] = []
    for idx, (heading, body) in enumerate(sections):
        key_parts = [extract_block_text(b) for b in body]
        key = "\n".join(p for p in key_parts if p).strip()
        if not key:
            kept_indices.append(idx)
            continue
        if key in seen:
            older_idx = seen[key]
            older_heading, older_body = sections[older_idx]
            to_delete.append(older_heading)
            to_delete.extend(older_body)
            if older_idx in kept_indices:
                kept_indices.remove(older_idx)
        seen[key] = idx
        kept_indices.append(idx)

    deleted_ids: List[str] = []
    for block in to_delete:
        block_id = block.get("id")
        if not block_id:
            continue
        if getattr(args, "dry_run", False):
            deleted_ids.append(block_id)
            continue
        try:
            client.delete_block(block_id)
            deleted_ids.append(block_id)
        except NotionError as exc:
            print(f"WARN: failed to delete block {block_id}: {exc}", file=sys.stderr)

    payload = {
        "wiki_page_id": page_id,
        "dry_run": getattr(args, "dry_run", False),
        "sections_total": len(sections),
        "sections_kept": len(kept_indices),
        "blocks_removed": len(deleted_ids),
        "removed_block_ids": deleted_ids,
    }
    print(json.dumps(audit_success("cleanup-wiki-page", payload), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal Notion Wiki compiler for llmwiki")
    parser.add_argument("--env-file", default=str(ENV_PATH), help="Path to .env file")
    parser.add_argument("--mapping", default="schema/notion_wiki_mapping.example.json", help="Mapping JSON path relative to llmwiki root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect-schema")
    inspect_parser.add_argument("--database", choices=["raw", "wiki"], default="wiki")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--title-property")

    compile_parser = subparsers.add_parser("compile-from-raw")
    compile_parser.add_argument("page_id")
    compile_parser.add_argument("--title")
    compile_parser.add_argument("--canonical-id")
    compile_parser.add_argument("--verification")
    compile_parser.add_argument("--compounded-level", type=float)
    compile_parser.add_argument("--last-compounded-at")
    compile_parser.add_argument("--append-heading")
    compile_parser.add_argument("--increment-compounded-level", action="store_true")
    compile_parser.add_argument("--title-property")
    compile_parser.add_argument("--canonical-id-property")
    compile_parser.add_argument("--verification-property")
    compile_parser.add_argument("--compounded-level-property")
    compile_parser.add_argument("--last-compounded-at-property")
    compile_parser.add_argument("--raw-title-property")
    compile_parser.add_argument("--raw-source-url-property")
    compile_parser.add_argument("--raw-status-property")
    compile_parser.add_argument("--raw-processed-at-property")
    compile_parser.add_argument("--raw-target-wiki-page-property")
    compile_parser.add_argument("--raw-compiled-status")
    compile_parser.add_argument("--force", action="store_true")
    compile_parser.add_argument("--auto-refine", action="store_true")
    compile_parser.add_argument("--strict-alias", action="store_true")
    compile_parser.add_argument("--strict-fuzzy", action="store_true")
    compile_parser.add_argument("--emit-diff", action="store_true")
    compile_parser.add_argument("--merge-mode", choices=["append", "propose", "replace"], default="append",
                                help="append: default, append 增量更新 block; propose: readonly preview; replace: replace content under --replace-heading")
    compile_parser.add_argument("--replace-heading", help="heading text to replace content under (only used with --merge-mode=replace)")

    queue_parser = subparsers.add_parser("compile-queue")
    queue_parser.add_argument("--status", default="Not started")
    queue_parser.add_argument("--limit", type=int, default=10)
    queue_parser.add_argument("--canonical-id")
    queue_parser.add_argument("--verification")
    queue_parser.add_argument("--compounded-level", type=float)
    queue_parser.add_argument("--last-compounded-at")
    queue_parser.add_argument("--append-heading")
    queue_parser.add_argument("--increment-compounded-level", action="store_true")
    queue_parser.add_argument("--title-property")
    queue_parser.add_argument("--canonical-id-property")
    queue_parser.add_argument("--verification-property")
    queue_parser.add_argument("--compounded-level-property")
    queue_parser.add_argument("--last-compounded-at-property")
    queue_parser.add_argument("--raw-title-property")
    queue_parser.add_argument("--raw-source-url-property")
    queue_parser.add_argument("--raw-status-property")
    queue_parser.add_argument("--raw-processed-at-property")
    queue_parser.add_argument("--raw-target-wiki-page-property")
    queue_parser.add_argument("--raw-compiled-status")
    queue_parser.add_argument("--force", action="store_true")
    queue_parser.add_argument("--auto-refine", action="store_true")
    queue_parser.add_argument("--strict-alias", action="store_true")
    queue_parser.add_argument("--strict-fuzzy", action="store_true")
    queue_parser.add_argument("--emit-diff", action="store_true")
    queue_parser.add_argument("--retry-failed", action="store_true")
    queue_parser.add_argument("--filter", action="append", default=[], help="Repeatable; additional Raw Inbox filter as PROP=VALUE")
    queue_parser.add_argument("--merge-mode", choices=["append", "propose", "replace"], default="append")
    queue_parser.add_argument("--replace-heading", help="Only used with --merge-mode=replace")

    upsert_parser = subparsers.add_parser("upsert-note")
    upsert_parser.add_argument("--title", required=True)
    upsert_parser.add_argument("--note", required=True)
    upsert_parser.add_argument("--source-url")
    upsert_parser.add_argument("--canonical-id")
    upsert_parser.add_argument("--verification")
    upsert_parser.add_argument("--compounded-level", type=float)
    upsert_parser.add_argument("--last-compounded-at")
    upsert_parser.add_argument("--append-heading")
    upsert_parser.add_argument("--increment-compounded-level", action="store_true")
    upsert_parser.add_argument("--title-property")
    upsert_parser.add_argument("--canonical-id-property")
    upsert_parser.add_argument("--verification-property")
    upsert_parser.add_argument("--compounded-level-property")
    upsert_parser.add_argument("--last-compounded-at-property")
    upsert_parser.add_argument("--strict-alias", action="store_true")
    upsert_parser.add_argument("--strict-fuzzy", action="store_true")

    session_parser = subparsers.add_parser("log-session-event")
    session_parser.add_argument("--model", required=True, help="Model id making the decision (e.g. claude-opus-4-7)")
    session_parser.add_argument("--raw-page-id", default="", help="Raw Inbox page id the decision concerns (optional)")
    session_parser.add_argument("--wiki-page-id", default="", help="Wiki page id the decision concerns (optional)")
    session_parser.add_argument("--tier", required=True, help="Match tier: canonical_id|title|alias|fuzzy|none")
    session_parser.add_argument("--decision", required=True, help="Action taken: update|create|ask_user|skip")
    session_parser.add_argument("--risk", default="low", help="Risk level: low|medium|high")
    session_parser.add_argument("--notes", default="", help="Free-form explanation")
    session_parser.add_argument("--input-json", default="", help="Optional JSON blob of input context (candidates, excerpts)")

    cleanup_parser = subparsers.add_parser("cleanup-wiki-page")
    cleanup_parser.add_argument("page_id")
    cleanup_parser.add_argument("--heading-prefix", default="增量更新", help="Only de-duplicate sections whose heading_2 starts with this prefix")
    cleanup_parser.add_argument("--dry-run", action="store_true", help="Report what would be deleted without calling Notion delete API")

    check_parser = subparsers.add_parser("check-editorial")
    check_parser.add_argument("page_id", nargs="?", default="", help="Wiki page id to check; omit when using --all")
    check_parser.add_argument("--all", action="store_true", help="Check every page in the Wiki database (bounded by --limit)")
    check_parser.add_argument("--limit", type=int, default=50, help="Max pages to inspect when --all is set")

    consolidate_parser = subparsers.add_parser("consolidate-evidence")
    consolidate_parser.add_argument("page_id")
    consolidate_parser.add_argument("--heading", default="原文证据", help="Heading text to consolidate under (default: 原文证据)")
    consolidate_parser.add_argument("--keep", type=int, default=4, help="Number of evidence items to keep (default: 4 per EDITORIAL_POLICY)")
    consolidate_parser.add_argument("--dry-run", action="store_true")

    rewrite_parser = subparsers.add_parser("rewrite-section")
    rewrite_parser.add_argument("page_id")
    rewrite_parser.add_argument("--heading", required=True, help="Heading text to rewrite under (heading_2 or heading_3)")
    rewrite_parser.add_argument("--body", required=True, help="New body text to place under the heading (paragraphs chunked on newlines)")
    rewrite_parser.add_argument("--promote", action="store_true", help="After rewriting, remove first paragraph starting with <placeholder> marker to promote from placeholder to real page")
    rewrite_parser.add_argument("--mention-map", help="Comma-separated label=page_id pairs; literal label occurrences in body become Notion page references")
    rewrite_parser.add_argument("--link-style", choices=["mention", "link", "both"], default="link",
                                help="mention: Notion page mention (semantic but UI under-renders API-created ones); link: text with notion.so href (robust blue clickable text); both: mention + link")
    rewrite_parser.add_argument("--dry-run", action="store_true")

    link_concepts_parser = subparsers.add_parser("link-concepts-in-page")
    link_concepts_parser.add_argument("page_id")
    link_concepts_parser.add_argument("--mention-map", required=True, help="Comma-separated label=page_id pairs to auto-link throughout the page")
    link_concepts_parser.add_argument("--link-style", choices=["mention", "link", "both"], default="link")
    link_concepts_parser.add_argument("--force", action="store_true", help="Rewrite blocks even if they already contain a link/mention (default: skip)")
    link_concepts_parser.add_argument("--dry-run", action="store_true")

    link_parser = subparsers.add_parser("link-pages")
    link_parser.add_argument("page_id")
    link_parser.add_argument("--add", action="append", default=[], help="Wiki page id to add to Related Pages relation (repeatable)")
    link_parser.add_argument("--remove", action="append", default=[], help="Wiki page id to remove from Related Pages relation (repeatable)")
    link_parser.add_argument("--ensure-property", action="store_true", help="Create Related Pages relation property on Wiki DB if missing")
    link_parser.add_argument("--dry-run", action="store_true")

    reference_parser = subparsers.add_parser("reference-check")
    reference_parser.add_argument("reference_page_id", help="Wiki page acting as exemplar (e.g., QueryLoop once refined to green)")
    reference_parser.add_argument("target_page_id", nargs="?", default="", help="Page to compare to reference; omit when using --all")
    reference_parser.add_argument("--all", action="store_true", help="Compare every page in the Wiki database (excluding reference itself)")
    reference_parser.add_argument("--limit", type=int, default=50)

    seed_parser = subparsers.add_parser("seed-related-pages")
    seed_parser.add_argument("source_page_id", help="Source wiki page whose inferred related concepts seed placeholder pages")
    seed_parser.add_argument("--dry-run", action="store_true", help="List would-be-created concepts without calling Notion create API")

    lint_parser = subparsers.add_parser("lint")
    lint_parser.add_argument("--title-property")
    lint_parser.add_argument("--verification-property")
    lint_parser.add_argument("--expired-values", nargs="*")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    env = load_env(Path(args.env_file))
    token = require_env(env, "NOTION_API_KEY")
    raw_database_id = optional_env(env, "NOTION_RAW_INBOX_DB_ID")
    wiki_database_id = optional_env(env, "NOTION_WIKI_DB_ID")
    mapping = load_mapping(args.mapping)
    client = NotionClient(token)

    if args.command == "inspect-schema":
        if args.database == "raw":
            return inspect_schema(client, require_env(env, "NOTION_RAW_INBOX_DB_ID"), "raw")
        return inspect_schema(client, require_env(env, "NOTION_WIKI_DB_ID"), "wiki")
    if args.command == "search":
        return command_search(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "compile-from-raw":
        return command_compile_from_raw(
            client,
            require_env(env, "NOTION_RAW_INBOX_DB_ID"),
            require_env(env, "NOTION_WIKI_DB_ID"),
            mapping,
            args,
        )
    if args.command == "compile-queue":
        return command_compile_queue(
            client,
            require_env(env, "NOTION_RAW_INBOX_DB_ID"),
            require_env(env, "NOTION_WIKI_DB_ID"),
            mapping,
            args,
        )
    if args.command == "upsert-note":
        return command_upsert(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "log-session-event":
        return command_log_session_event(args)
    if args.command == "cleanup-wiki-page":
        return command_cleanup_wiki_page(client, args)
    if args.command == "check-editorial":
        return command_check_editorial(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "consolidate-evidence":
        return command_consolidate_evidence(client, args)
    if args.command == "rewrite-section":
        return command_rewrite_section(client, args)
    if args.command == "link-pages":
        return command_link_pages(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "link-concepts-in-page":
        return command_link_concepts_in_page(client, args)
    if args.command == "reference-check":
        return command_reference_check(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "seed-related-pages":
        return command_seed_related_pages(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "lint":
        return command_lint(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NotionError as exc:
        try:
            append_audit_event(
                {
                    "timestamp": iso_now(),
                    "command": detect_command_name(sys.argv[1:]),
                    "status": "error",
                    "error": str(exc),
                }
            )
        except Exception:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
