#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import re
import sys
import http.client
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
RAW_DUMPS_DIR = ROOT / "raw" / "notion_dumps"
DEFAULT_NOTION_VERSION = "2022-06-28"
DEFAULT_MAX_QUERY_PAGES = 25
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-reasoner"

LLM_PROVIDERS: Dict[str, Dict[str, str]] = {
    "deepseek": {
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-reasoner",
        "env_key": "DEEPSEEK_API_KEY",
        "env_key_file": "DEEPSEEK_API_KEY_FILE",
    },
    "kimi": {
        "endpoint": "https://api.moonshot.cn/v1/chat/completions",
        "default_model": "kimi-k2.6",
        "env_key": "KIMI_API_KEY",
        "env_key_file": "KIMI_API_KEY_FILE",
        "fixed_temperature": "1.0",
    },
    "gemini": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "default_model": "gemini-2.5-flash",
        "env_key": "GEMINI_API_KEY",
        "env_key_file": "GEMINI_API_KEY_FILE",
    },
    # deepseek-chat provider: shares deepseek credentials but points at the
    # fast chat model for judge-role calls (cheap + fast categorical judgments,
    # not deep reasoning). See judge_chat() for usage.
    "deepseek-chat": {
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
        "env_key_file": "DEEPSEEK_API_KEY_FILE",
    },
}


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


class LLMClient:
    def __init__(self, api_key: str, endpoint: str, model: str, provider: str = "deepseek", fixed_temperature: Optional[float] = None):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.provider = provider
        self.fixed_temperature = fixed_temperature

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 10000,
        temperature: float = 0.4,
    ) -> Dict[str, Any]:
        effective_temperature = self.fixed_temperature if self.fixed_temperature is not None else temperature
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": effective_temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers=headers,
        )
        # Retry on transient network/chunked-read errors with exponential backoff.
        # HTTP 4xx (client errors, e.g. 400 / 401 / 404) never retry — they're caller bugs.
        # HTTP 5xx + IncompleteRead + connection reset / URLError retry up to max_attempts.
        max_attempts = 3
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                # Only retry on 5xx; surface 4xx immediately
                if 500 <= exc.code < 600 and attempt < max_attempts:
                    last_exc = NotionError(f"{self.provider} HTTP {exc.code} (attempt {attempt}/{max_attempts}): {detail[:300]}")
                    print(f"WARN: {last_exc}; retrying in {attempt * 2}s", file=sys.stderr)
                    time.sleep(attempt * 2)
                    continue
                raise NotionError(f"{self.provider} HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, http.client.IncompleteRead, ConnectionError, TimeoutError) as exc:
                last_exc = exc
                if attempt < max_attempts:
                    print(f"WARN: {self.provider} transient error (attempt {attempt}/{max_attempts}): {exc}; retrying in {attempt * 2}s", file=sys.stderr)
                    time.sleep(attempt * 2)
                    continue
                raise NotionError(f"{self.provider} network error after {max_attempts} attempts: {exc}") from exc
            except json.JSONDecodeError as exc:
                last_exc = exc
                if attempt < max_attempts:
                    print(f"WARN: {self.provider} JSON decode error (attempt {attempt}/{max_attempts}): {exc}; retrying in {attempt * 2}s", file=sys.stderr)
                    time.sleep(attempt * 2)
                    continue
                raise NotionError(f"{self.provider} JSON decode failure after {max_attempts} attempts: {exc}") from exc
        # Unreachable; loop either returned or raised
        raise NotionError(f"{self.provider} failed after {max_attempts} attempts: {last_exc}")


# Backwards compat alias
DeepSeekClient = LLMClient


def resolve_llm_key(env: Dict[str, str], provider: str) -> str:
    cfg = LLM_PROVIDERS.get(provider)
    if not cfg:
        raise NotionError(f"unknown provider {provider!r}; choose from {sorted(LLM_PROVIDERS)}")
    key = env.get(cfg["env_key"], "").strip()
    if key:
        return key
    key_file = env.get(cfg["env_key_file"], "").strip()
    if key_file:
        path = Path(key_file)
        if not path.exists():
            raise NotionError(f"{cfg['env_key_file']} points to missing path: {path}")
        return path.read_text(encoding="utf-8").strip()
    raise NotionError(
        f"Set {cfg['env_key']} or {cfg['env_key_file']} in .env to use provider {provider!r}"
    )


def build_llm_client(env: Dict[str, str], provider: str, model_override: Optional[str] = None) -> LLMClient:
    cfg = LLM_PROVIDERS.get(provider)
    if not cfg:
        raise NotionError(f"unknown provider {provider!r}")
    fixed_temp_raw = cfg.get("fixed_temperature")
    fixed_temp = float(fixed_temp_raw) if fixed_temp_raw is not None else None
    return LLMClient(
        api_key=resolve_llm_key(env, provider),
        endpoint=cfg["endpoint"],
        model=model_override or cfg["default_model"],
        provider=provider,
        fixed_temperature=fixed_temp,
    )


def resolve_deepseek_key(env: Dict[str, str]) -> str:
    """Backwards compat shim."""
    return resolve_llm_key(env, "deepseek")


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
        "llm-refine",
        "llm-refine-page",
        "llm-validate",
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
    """Strip raw-bookkeeping prefixes/suffixes to get a clean wiki topic title.

    Handles these patterns (applied in order, each optional):
    - "第N章 X" → "X" (chapter prefix)
    - "真人测试 · X" / "真人测试-X" → "X" (annotation prefix)
    - "X Raw YYYY-MM-DD" → "X" (raw-inbox suffix pattern like "Smoke Test Raw 2026-04-21")
    - trailing " YYYY-MM-DD" → stripped
    - "X：Y" or "X:Y" → "X" (colon split, keep left)
    """
    title = raw_title.strip()
    original = title
    chapter_match = re.match(r"^第\s*\d+\s*章\s*(.+)$", title)
    if chapter_match:
        title = chapter_match.group(1).strip()
    annotation_match = re.match(r"^真人测试\s*[·・\-—:： ]+\s*(.+)$", title)
    if annotation_match:
        title = annotation_match.group(1).strip()
    raw_suffix_match = re.match(r"^(.+?)\s+Raw\s+\d{4}-\d{2}-\d{2}$", title, flags=re.IGNORECASE)
    if raw_suffix_match:
        title = raw_suffix_match.group(1).strip()
    title = re.sub(r"\s+\d{4}-\d{2}-\d{2}$", "", title)
    for delimiter in ("：", ":"):
        if delimiter in title:
            left, _right = title.split(delimiter, 1)
            left = left.strip()
            if 1 < len(left) <= 80:
                return left
    return title.strip() or original


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

    # Phase 2 support: if the caller's judge already decided the alias hit is
    # a different entity, drop alias matches here so downstream treats it as
    # new-page create.
    if getattr(args, "skip_alias_strategy", False):
        matched_candidates = [(p, s) for p, s in matched_candidates if s != "alias"]

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


def _ensure_wiki_source_contains_raw(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    wiki_page_id: str,
    raw_page_id: str,
) -> Optional[str]:
    """Union-append raw_page_id into Wiki.Source relation. Silent no-op if:
    - mapping lacks source_property
    - schema lacks the configured property
    - property is not a relation
    - property's relation target is not the raw inbox DB
    - raw_page_id already in the list (idempotent)
    Returns 'added' / 'already_present' / None (skipped)."""
    source_prop_name = mapping.get("source_property")
    if not source_prop_name:
        return None
    try:
        database = client.retrieve_database(wiki_database_id)
    except NotionError:
        return None
    prop_meta = database.get("properties", {}).get(source_prop_name)
    if not prop_meta or prop_meta.get("type") != "relation":
        return None
    try:
        wiki_page = client.retrieve_page(wiki_page_id)
    except NotionError:
        return None
    existing = wiki_page.get("properties", {}).get(source_prop_name, {}).get("relation", []) or []
    existing_ids = {normalize_notion_id(r.get("id", "")) for r in existing if r.get("id")}
    normalized_raw = normalize_notion_id(raw_page_id)
    if normalized_raw in existing_ids:
        return "already_present"
    new_relation = list(existing) + [{"id": raw_page_id}]
    try:
        client.update_page(wiki_page_id, {"properties": {source_prop_name: {"relation": new_relation}}})
    except NotionError as exc:
        print(f"WARN: failed to update Wiki.Source on {wiki_page_id}: {exc}", file=sys.stderr)
        return None
    return "added"


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
    skip_note_append = bool(getattr(args, "skip_note_append", False))
    if exact_match:
        properties = build_properties(database, mapping, args)
        if args.increment_compounded_level:
            level_prop_name = args.compounded_level_property or mapping.get("compounded_level_property")
            if level_prop_name and level_prop_name in database.get("properties", {}):
                current_number = exact_match.get("properties", {}).get(level_prop_name, {}).get("number") or 0
                properties[level_prop_name] = {"number": current_number + 1}
        client.update_page(exact_match["id"], {"properties": properties})
        if not skip_note_append:
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
    }
    if not skip_note_append:
        payload["children"] = build_append_blocks(args.note, append_heading, args.source_url)
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
    # Previous logic used `any(marker not in body)` which always re-triggered
    # when `build_deepening_blocks` conditionally skipped markers (e.g. 实现信号
    # only appears for topics matching the agent/system topic map). Result: every
    # --force-refine run appended another 补充整理 wrapper, accumulating 4+ stale
    # copies on 量化入门 / Pop Mart pages. Gate on the wrapper heading itself
    # instead — one 补充整理 per page, period.
    if "补充整理" not in existing_body_text:
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
    env: Optional[Dict[str, str]] = None,
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

    # compile-from-raw no longer dumps raw body to wiki by default (option A,
    # 2026-04-23). Raw material lives only on raw page; wiki holds refined
    # content only; provenance via Wiki.Source relation + compile-log.jsonl.
    # --append-raw-body-to-wiki flag opts back into the legacy behavior.
    append_raw_body = bool(getattr(args, "append_raw_body_to_wiki", False))
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
        skip_note_append=not append_raw_body,
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

        _ensure_wiki_source_contains_raw(client, wiki_database_id, mapping, wiki_page_id, raw_page_id)

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
    # Phase 2: if the candidate match would be via `alias`, pre-run deepseek-chat
    # judge to decide if it's really the same entity. different_entity → force a
    # new page by flagging skip_alias_strategy (find_upsert_target will drop
    # alias matches). Enabled by default when judge LLM is available; bypass
    # via --no-judge. Never fires under --strict-alias (that already hard-stops).
    judge_result: Optional[Dict[str, Any]] = None
    disable_judge = bool(getattr(args, "no_judge", False))
    strict_alias = bool(getattr(args, "strict_alias", False))
    if not disable_judge and not strict_alias and env is not None:
        try:
            wiki_database = client.retrieve_database(wiki_database_id)
            preview_target = find_upsert_target(client, wiki_database, wiki_database_id, mapping, upsert_args)
        except NotionError:
            preview_target = None
        if preview_target and preview_target.get("match_strategy") == "alias" and preview_target.get("exact_match"):
            wiki_page = preview_target["exact_match"]
            wiki_page_id_peek = wiki_page.get("id", "")
            title_prop_peek = mapping.get("title_property") or detect_title_property(wiki_database) or "Name"
            aliases_prop_peek = mapping.get("aliases_property") or ""
            wiki_title_peek = extract_title(wiki_page, title_prop_peek) if title_prop_peek else ""
            wiki_aliases_peek = extract_property_text(wiki_page, aliases_prop_peek) if aliases_prop_peek else ""
            try:
                wiki_body_peek = read_page_body_text(client, wiki_page_id_peek)[:1200]
            except NotionError:
                wiki_body_peek = ""
            judge_result = judge_alias_match(
                env,
                title,
                note,
                wiki_title_peek,
                wiki_aliases_peek,
                wiki_body_peek,
                wiki_page_id_peek,
            )
            if judge_result["choice"] == "different_entity" and judge_result["confidence"] >= 0.6:
                upsert_args.skip_alias_strategy = True
                print(
                    f"INFO: deepseek-chat judge decided alias hit on {wiki_page_id_peek!r} is a different entity "
                    f"(confidence {judge_result['confidence']:.2f}); creating new page instead. "
                    f"Reasoning: {judge_result['reasoning'][:200]}",
                    file=sys.stderr,
                )

    wiki_result = upsert_note_to_wiki(client, wiki_database_id, mapping, upsert_args)

    # Union-append this raw_page_id into Wiki.Source relation. Keeps "this wiki
    # object was built from these raws" auditable on the Notion side and
    # supports object-level compounding history (same wiki updated by multiple
    # raws over time). Silent no-op if the wiki DB has no Source property or
    # the property isn't a relation.
    _ensure_wiki_source_contains_raw(client, wiki_database_id, mapping, wiki_result["page_id"], raw_page_id)

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
        "judge": (
            {
                "kind": "alias_match",
                "choice": judge_result["choice"],
                "confidence": judge_result["confidence"],
                "reasoning": judge_result["reasoning"][:300],
            }
            if judge_result
            else None
        ),
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
    env: Optional[Dict[str, str]] = None,
) -> int:
    payload = compile_raw_page(client, raw_database_id, wiki_database_id, mapping, args, args.page_id, env=env)
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
    queue_env = getattr(args, "_env", None)
    for page_id in raw_page_ids:
        if not page_id:
            continue
        try:
            payload = compile_raw_page(client, raw_database_id, wiki_database_id, mapping, args, page_id, env=queue_env)
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


LLM_LIST_SECTION_SPEC: Dict[str, Dict[str, Any]] = {
    "关键机制": {
        "min_items": 3,
        "max_items": 5,
        "item_hint": "X 背后的一条机制要点",
        "max_chars_per_item": 150,
    },
    "实现信号": {
        "min_items": 3,
        "max_items": 5,
        "item_hint": "一个可被外部 observe 的具体信号",
        "max_chars_per_item": 150,
    },
}


def parse_list_response(text: str) -> Optional[List[str]]:
    """Try to parse an LLM response as a list of strings.

    Accepts:
      1. JSON object with 'items' key: {"items": ["...", "..."]}
      2. Bare JSON array: ["...", "..."]
      3. Markdown-ish bullet lines (fallback)
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
        if fence_match:
            stripped = fence_match.group(1).strip()

    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict) and "items" in obj:
            items = obj["items"]
            if isinstance(items, list) and all(isinstance(x, str) for x in items):
                return [s.strip() for s in items if s.strip()]
        if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
            return [s.strip() for s in obj if s.strip()]
    except (json.JSONDecodeError, ValueError):
        pass

    obj_match = re.search(r"\{.*?\"items\"\s*:\s*\[.*?\]\s*\}", stripped, re.DOTALL)
    if obj_match:
        try:
            obj = json.loads(obj_match.group(0))
            items = obj.get("items")
            if isinstance(items, list) and all(isinstance(x, str) for x in items):
                return [s.strip() for s in items if s.strip()]
        except (json.JSONDecodeError, ValueError):
            pass

    arr_match = re.search(r"\[\s*\".+?\"\s*(?:,\s*\".+?\"\s*)*\]", stripped, re.DOTALL)
    if arr_match:
        try:
            arr = json.loads(arr_match.group(0))
            if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                return [s.strip() for s in arr if s.strip()]
        except (json.JSONDecodeError, ValueError):
            pass

    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    bullet_pat = re.compile(r"^[-*•]\s*(.+)$|^\d+[\.\)]\s*(.+)$")
    extracted: List[str] = []
    for line in lines:
        m = bullet_pat.match(line)
        if m:
            extracted.append((m.group(1) or m.group(2)).strip())
    if len(extracted) >= 2:
        return extracted

    return None


LLM_SECTION_ROLE_GUIDANCE: Dict[str, str] = {
    "定义": (
        "这是「定义」段。职责：用 1-2 句话说清楚 X 是什么，让读者有一个初步的锚点。\n"
        "不要：不展开判据 / 不讲相邻概念张力 / 不做类比回溯 / 不列实现信号 / 不超过 3 句。\n"
        "这一段追求简明精确，不追求解读深度——后续段（为什么重要 / 核心判断等）会负责深度。"
    ),
    "为什么重要": (
        "这是「为什么重要」段。职责：告诉读者为什么要关心 X——它改变了什么思考方式 / 什么问题因 X 变得可解。\n"
        "不要：不重复定义 / 不给测试判据 / 不讲相邻概念张力 / 不列实现信号。\n"
        "篇幅 1-2 段；可用一个类比帮读者建立直觉，但不要完整走三段结构。"
    ),
    "关键机制": (
        "这是「关键机制」段。职责：列出 X 背后运行的几条机制要点（3-5 条），每条一两句。\n"
        "形式：条目列表 / bulleted 式叙述，不是连续大段叙事。\n"
        "不要：不重复定义 / 不做可操作测试方式 / 不完整讲相邻概念 / 不要为每条加类比。\n"
        "只开一句总领句，然后逐条机制，每条紧凑。"
    ),
    "核心判断": (
        "这是「核心判断」段。职责：给出关于 X 最不显见的那个判断——读者没想到的那层，带一条可操作测试方式。\n"
        "这是全页唯一需要深度 \"解读\" 的段，可以走完整的 style J 三段结构（观察 → 类比 → 回溯映射）。\n"
        "不要：不重复定义 / 不再列机制 / 不写相邻概念完整对比（那是下一段的事）。"
    ),
    "实现信号": (
        "这是「实现信号」段。职责：列出一个系统里到底有没有 X 的可观察证据（3-5 条）。\n"
        "形式：条目列表，每条一行或一两句，应当是可被外部 observe 的具体现象（不是抽象原则）。\n"
        "不要：不重复定义 / 不做完整测试判据解说 / 不讲相邻概念 / 不加类比铺垫。\n"
        "读者读完这几条能立刻拿去对照任何具体系统。"
    ),
    "与相邻概念的区别": (
        "这是「与相邻概念的区别」段。职责：只做 X 和某一个具体相邻概念（如 QueryEngine / State Management）的分界，说清谁持有什么、谁负责什么。\n"
        "不要：不重做定义 / 不再列机制 / 不再讲核心判断 / 不做完整测试方式。\n"
        "篇幅 1-2 段。可用一次类比做对照，但要紧扣边界划分。"
    ),
}


# Heuristic prompt fragments (2026-04-23 refactor)
# Replaces the previous monolithic LLM_REFINE_SYSTEM_PROMPT_BASE +
# LLM_REFINE_DEFAULT_STYLE_NOTE, which hardcoded "agent engineer reader" +
# "LangChain/ReAct anchor" and thus polluted non-agent topics (e.g. 量化入门
# got written with LangChain / ReAct / AutoGPT analogies).
#
# The replacement is composed from 5 reusable fragments:
#   base_quality — topic-neutral self-check questions (提要 vs 解读)
#   reader_profile[role] — swappable reader assumption (agent / quant / general)
#   anchor_hint — topic-agnostic hint: open from something the reader already knows
#   analogy_hint — topic-agnostic analogy craft hints (no enumerated pool)
#   taboo_hint — self-check pitfalls, no rigid rules
#
# All fragments use heuristic ("问问自己 / 试试 / 举例")不是规定 ("必须 / 严禁") 口吻，
# giving the LLM room to pick anchors and analogies that fit the topic.

PROMPT_FRAGMENT_PREAMBLE = "你是永久笔记的编辑器。"

PROMPT_FRAGMENT_BASE_QUALITY = (
    "写之前先自查三个问题：\n"
    "- 这段有没有一句话能被读者反驳？（只有可被反驳的论断才是判断，不是提要）\n"
    "- 读者想验证结论，给得出可操作判据吗？（\"测试方式：...\"、\"判据是：...\"）\n"
    "- 有没有指出读者容易搞错或相邻概念容易混淆的那条线？\n"
    "三个都没有，这段大概率又写成提要了。"
)

PROMPT_FRAGMENT_ANCHOR_HINT = (
    "开头可以试着从读者已经熟悉的东西切入：\n"
    "- 他已经用过的工具\n"
    "- 他见过的现象\n"
    "- 他理解的概念\n"
    "再自然过渡到本主题。切忌从抽象定义起头，或强拉一个读者不熟、且和本主题不贴合的框架。"
)

PROMPT_FRAGMENT_ANALOGY_HINT = (
    "写类比时可以问问自己：\n"
    "- 读者在什么日常场景下会遇到\"同样的问题结构\"？\n"
    "- 类比里的每个元素能不能一一对应本主题？对不上就说明不贴合，换一个\n"
    "- 类比讲完能不能自然回到主题？（不能停在\"就像 XX 一样\"这种类比收尾）\n"
    "\n"
    "举例思路（示意，不要照抄）：讲 agent loop 一些人用游戏存档；讲量化回测一些人用历史复盘；"
    "讲数据库事务一些人用银行转账。如果你发现本主题找不到自然的类比，写一句\"本段不强行类比\" "
    "也比生拉硬扯好。"
)

PROMPT_FRAGMENT_TABOO_HINT = (
    "自查常见陷阱（自然避开即可，不必逐条对照）：\n"
    "- \"关键 / 核心 / 重要\"没配具体内容 → 空洞\n"
    "- \"颠覆 / 革命 / breakthrough\" → 营销话术\n"
    "- 段落以类比结尾没回溯 → 类比断线\n"
    "- 小明小红类玩具比喻 → 读者代入不进去\n"
    "- 硬拉不相关领域的术语当类比（典型：用 agent 框架解释量化主题、用交易术语解释产品设计） → 污染"
)

PROMPT_FRAGMENT_OUTPUT_SHAPE = (
    "输出纯文本段落，空行分段。不要返回 heading、不要前言、不要 markdown、不要解释你做了什么。"
)

READER_PROFILES: Dict[str, str] = {
    "agent": "读者：懂 agent 系统架构的工程师 / 产品人（熟悉 LangChain / ReAct 基础）；已看过教科书，需要对自己思维框架的扩展。",
    "quant": "读者：懂量化与回测的投资者 / 工程师（熟悉均线 / 因子 / 止损 / 回测基础）；已看过入门教材，需要对判据和策略边界的深入。",
    "general": "读者：有技术背景但不限领域；避免该主题专属术语堆砌，用通用工程思路即可。",
}

# Hard profile-specific taboos — NOT heuristic. These apply when a non-agent
# topic is being refined and the model's training distribution would otherwise
# pull in AI-framework terms. Heuristic hints in taboo_hint weren't sufficient
# to stop Kimi from reaching for LangChain / AgentExecutor / AutoGPT analogies
# on 量化入门; this fragment is a hard prohibition inserted after taboo_hint.
PROFILE_HARD_TABOOS: Dict[str, str] = {
    "agent": "",  # agent profile wants AI-framework anchors; no cross-domain ban
    "quant": (
        "**跨领域污染硬禁（严格遵守）**：本主题是量化 / 金融领域，读者不需要 AI agent 语境。"
        "禁止以下术语作为锚点或类比：LangChain / ReAct / AgentExecutor / AutoGPT / "
        "ChatGPT prompt / tool use / agent loop / 工具调用循环。"
        "也不要用 Git / fork / WAL / 数据库事务 这类纯工程术语做类比。"
        "如果类比只能从这些领域找，改写为\"本段不强行类比\"或直接回到主题本身的实践（回测 / 止损 / 因子 / K线）。"
    ),
    "general": (
        "**跨领域污染硬禁（严格遵守）**：本主题与 AI agent、金融量化都无关。"
        "禁止以下术语作为锚点或类比：LangChain / ReAct / AgentExecutor / AutoGPT / 工具调用循环 / "
        "均线 / 止损 / 回测 / K线 / 金叉死叉。"
        "如果无法从本主题自身找到自然类比，写\"本段不强行类比\"。"
    ),
}

VALID_READERS = set(READER_PROFILES.keys())


def infer_reader_profile(text: str) -> str:
    """Heuristic classifier: pick a reader profile from content keywords.

    Not a semantic classifier — just keyword density. If nothing triggers
    strongly, fall back to 'general' (safe neutral default)."""
    if not text:
        return "general"
    t = text.lower()
    agent_tokens = ("agent", "langchain", "react agent", "agentexecutor", "autogpt", "tool use", "工具调用", "代理", "queryloop", "queryengine")
    quant_tokens = ("量化", "均线", "回测", "因子", "止损", "macd", "rsi", "boll", "k线", "sma", "ema", "金叉", "死叉", "仓位", "技术指标")
    agent_score = sum(1 for k in agent_tokens if k in t)
    quant_score = sum(1 for k in quant_tokens if k in t)
    if quant_score >= 2 and quant_score > agent_score:
        return "quant"
    if agent_score >= 2 and agent_score > quant_score:
        return "agent"
    return "general"


def _resolve_reader_for_llm_refine(
    notion_client: "NotionClient",
    args: argparse.Namespace,
    page_id: Optional[str] = None,
) -> str:
    """Pick reader profile: --reader flag wins; else auto-infer from wiki page body."""
    explicit = getattr(args, "reader", None)
    if explicit and explicit in VALID_READERS:
        return explicit
    if not page_id:
        return "general"
    try:
        body_text = read_page_body_text(notion_client, page_id)
    except NotionError:
        return "general"
    return infer_reader_profile(body_text or "")


def build_llm_refine_system_prompt(
    style_samples: List[Dict[str, str]],
    style_note: str,
    reader: str = "general",
) -> str:
    """Compose a heuristic system prompt from swappable fragments.

    Only reader_profile varies by topic (swappable via `reader`); the rest
    (base quality / anchor hint / analogy hint / taboo hint / output shape)
    are topic-neutral and共用. Optional extra style_note is appended as-is so
    existing `--style-note` CLI usage continues to work.
    """
    reader_key = reader if reader in VALID_READERS else "general"
    sections: List[str] = [
        PROMPT_FRAGMENT_PREAMBLE,
        READER_PROFILES[reader_key],
        PROMPT_FRAGMENT_BASE_QUALITY,
        PROMPT_FRAGMENT_ANCHOR_HINT,
        PROMPT_FRAGMENT_ANALOGY_HINT,
        PROMPT_FRAGMENT_TABOO_HINT,
    ]
    hard_taboo = PROFILE_HARD_TABOOS.get(reader_key, "").strip()
    if hard_taboo:
        sections.append(hard_taboo)
    sections.append(PROMPT_FRAGMENT_OUTPUT_SHAPE)
    if style_samples:
        lines = [
            "## 读者认可的样本风格",
            "以下是读者已经认可的\"有解读\"写法。模仿论断密度、判据方式、段落节奏：",
        ]
        for i, sample in enumerate(style_samples, 1):
            lines.append(f"### 样本 {i}：{sample.get('label', '')}")
            lines.append(sample.get("text", "").strip())
        sections.append("\n".join(lines))
    if style_note and style_note.strip():
        sections.append("## 本次额外的风格要求\n" + style_note.strip())
    return "\n\n".join(sections)


def fetch_style_samples(
    client: NotionClient,
    style_from_page_id: str,
    sample_headings: Tuple[str, ...] = ("定义", "核心判断", "关联概念"),
) -> List[Dict[str, str]]:
    blocks = iterate_block_children(client, style_from_page_id)
    samples: List[Dict[str, str]] = []
    for heading_text in sample_headings:
        _, body = find_section_body(blocks, heading_text)
        text = "\n\n".join(
            extract_block_text(b) for b in body if extract_block_text(b).strip()
        ).strip()
        if text:
            samples.append({"label": heading_text, "text": text})
    return samples


def command_llm_refine(
    notion_client: NotionClient,
    deepseek_client: DeepSeekClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    page_id = args.page_id
    heading = args.heading
    top_blocks = iterate_block_children(notion_client, page_id)
    heading_block, section_body = find_section_body(top_blocks, heading)
    if heading_block is None:
        raise NotionError(f"Heading {heading!r} not found on page {page_id!r}")

    current_text = "\n\n".join(
        extract_block_text(b) for b in section_body if extract_block_text(b).strip()
    ).strip()

    page = notion_client.retrieve_page(page_id)
    title_prop = mapping.get("title_property") or detect_title_property(
        notion_client.retrieve_database(wiki_database_id)
    )
    title = extract_title(page, title_prop) if title_prop else ""

    source_context = ""
    if getattr(args, "source_page_id", None):
        source_context = read_page_body_text(notion_client, args.source_page_id)

    list_spec = LLM_LIST_SECTION_SPEC.get(heading)
    user_prompt_parts: List[str] = [
        f"词条：{title or '(未命名)'}",
        f"待重写段 heading：{heading}",
    ]
    role_hint = LLM_SECTION_ROLE_GUIDANCE.get(heading)
    if role_hint:
        user_prompt_parts.extend(["", "本段职责（必须遵守，避免和其他段重叠）：", role_hint])
    user_prompt_parts.extend(
        [
            "",
            "当前段落内容（这一版过于提要化，请改写为有解读）：",
            "---",
            current_text or "(当前段为空)",
            "---",
        ]
    )
    if source_context:
        user_prompt_parts.extend(
            [
                "",
                "源页 / 相邻材料（供判据和张力参考）：",
                "---",
                source_context,
                "---",
            ]
        )
    if list_spec:
        user_prompt_parts.extend(
            [
                "",
                "**输出格式（必须严格遵守）**：",
                "你的回答必须是且仅是一个 JSON 对象，不要任何解释、不要 markdown 代码块、不要前言：",
                '{"items": ["第一条 ...", "第二条 ...", ...]}',
                f"- items 数组长度 {list_spec['min_items']}-{list_spec['max_items']} 条",
                f"- 每条内容是{list_spec['item_hint']}，紧凑 1-2 句（不超过 {list_spec['max_chars_per_item']} 字）",
                "- 不要在条目内部写 bullet 符号、序号或多段落",
                "- 不要类比 / 铺垫 / 回溯映射（这些留给其他段）",
                "- 纯 JSON，前后不要任何文字",
            ]
        )
    if getattr(args, "extra_instruction", ""):
        user_prompt_parts.extend(["", "额外要求：", args.extra_instruction])
    user_prompt = "\n".join(user_prompt_parts)

    style_samples: List[Dict[str, str]] = []
    if getattr(args, "style_from_page_id", None):
        style_samples = fetch_style_samples(notion_client, args.style_from_page_id)
    style_note = getattr(args, "style_note", "") or ""
    reader = _resolve_reader_for_llm_refine(notion_client, args, page_id=args.page_id)
    system_prompt = build_llm_refine_system_prompt(style_samples, style_note, reader=reader)

    response = deepseek_client.chat(
        system=system_prompt,
        user=user_prompt,
        max_tokens=getattr(args, "max_tokens", 10000),
        temperature=getattr(args, "temperature", 0.4),
    )
    choices = response.get("choices") or []
    if not choices:
        raise NotionError(f"DeepSeek returned no choices: {response}")
    message = choices[0].get("message", {})
    new_body = (message.get("content") or "").strip()
    reasoning = message.get("reasoning_content") or ""
    usage = response.get("usage") or {}

    if not new_body:
        raise NotionError(f"DeepSeek returned empty content; reasoning={reasoning[:200]!r}")

    if getattr(args, "preview", False):
        payload = {
            "wiki_page_id": page_id,
            "heading": heading,
            "model": deepseek_client.model,
            "preview": True,
            "generated_body": new_body,
            "reasoning": reasoning,
            "usage": usage,
        }
        print(json.dumps(audit_success("llm-refine", payload), ensure_ascii=False, indent=2))
        return 0

    deleted_ids: List[str] = []
    for b in section_body:
        bid = b.get("id")
        if not bid:
            continue
        try:
            notion_client.delete_block(bid)
            deleted_ids.append(bid)
        except NotionError as exc:
            print(f"WARN: failed to delete block {bid}: {exc}", file=sys.stderr)

    mention_map = parse_mention_map(getattr(args, "mention_map", None))
    link_style = getattr(args, "link_style", "link")
    new_blocks: List[Dict[str, Any]] = []
    render_mode = "paragraph"
    list_items: Optional[List[str]] = None
    if list_spec:
        list_items = parse_list_response(new_body)
    if list_items:
        render_mode = "bulleted_list_item"
        for item in list_items:
            for chunk in chunk_text(item):
                rich = (
                    build_rich_text_with_mentions(chunk, mention_map, link_style)
                    if mention_map
                    else rich_text_value(chunk)
                )
                new_blocks.append(
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {"rich_text": rich},
                    }
                )
    else:
        if list_spec:
            print(
                f"WARN: expected JSON list for heading {heading!r} but failed to parse; falling back to paragraph",
                file=sys.stderr,
            )
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", new_body) if p.strip()]
        if not paragraphs:
            paragraphs = [new_body]
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
    if new_blocks:
        notion_client.append_block_children(page_id, new_blocks, after=heading_block["id"])

    payload = {
        "wiki_page_id": page_id,
        "heading": heading,
        "model": deepseek_client.model,
        "deleted_block_count": len(deleted_ids),
        "new_block_count": len(new_blocks),
        "render_mode": render_mode,
        "list_item_count": len(list_items) if list_items else 0,
        "reasoning": reasoning,
        "usage": usage,
    }
    log_path = append_jsonl_log(
        daily_log_filename("llm-refine-log.jsonl"),
        {
            **payload,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "style_from_page_id": getattr(args, "style_from_page_id", None),
            "style_sample_count": len(style_samples),
            "style_note": style_note,
            "generated_body": new_body,
        },
    )
    payload["log_path"] = str(log_path)
    print(json.dumps(audit_success("llm-refine", payload), ensure_ascii=False, indent=2))
    return 0


def parse_whole_page_response(text: str) -> Optional[Dict[str, Any]]:
    """Parse an LLM whole-page response as {heading: {content|items}}."""
    stripped = text.strip()
    fence = re.match(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    for match in re.finditer(r"\{.*\}", stripped, re.DOTALL):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict) and obj:
                return obj
        except (json.JSONDecodeError, ValueError):
            continue
    return None


WHOLE_PAGE_CROSS_SECTION_DIRECTIVE = (
    "\n\n## 整页协调约束（必须遵守）\n"
    "- 每段使用**不同的锚点开场**：不要所有段都从 LangChain AgentExecutor 中断重启切入；可以轮换 LangChain / ReAct / AutoGPT / ChatGPT / 一般 agent 循环 / 没有 agent 的单次模型调用 等不同锚点\n"
    "- 每段使用**不同的类比**：如果段 A 用游戏存档，段 B 就不能再用游戏存档——应轮换 Google Docs 自动保存 / Notion 多端同步 / ChatGPT 会话历史 / 手机 APP 切后台 等\n"
    "- 禁止跨段内容重复：同一个测试判据 / 同一个结论句不能出现在两段里\n"
    "- 每段只做自己的职责（role 里的界定），不越界到其他段的内容范围\n"
)


def command_llm_refine_page(
    notion_client: NotionClient,
    deepseek_client: DeepSeekClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    page_id = args.page_id
    if getattr(args, "sections", ""):
        sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    else:
        sections = ["定义", "为什么重要", "关键机制", "核心判断", "实现信号", "与相邻概念的区别"]

    top_blocks = iterate_block_children(notion_client, page_id)
    database = notion_client.retrieve_database(wiki_database_id)
    title_prop = mapping.get("title_property") or detect_title_property(database)
    page = notion_client.retrieve_page(page_id)
    title = extract_title(page, title_prop) if title_prop else ""

    section_info: List[Tuple[str, Dict[str, Any], List[Dict[str, Any]], str]] = []
    for s in sections:
        heading_block, body = find_section_body(top_blocks, s)
        if heading_block is None:
            print(f"WARN: section {s!r} not found on page {page_id!r}, skip", file=sys.stderr)
            continue
        current = "\n\n".join(
            extract_block_text(b) for b in body if extract_block_text(b).strip()
        ).strip()
        section_info.append((s, heading_block, body, current))
    if not section_info:
        raise NotionError("no recognized sections on page to refine")

    source_context = ""
    if getattr(args, "source_page_id", None):
        source_context = read_page_body_text(notion_client, args.source_page_id)

    style_samples: List[Dict[str, str]] = []
    if getattr(args, "style_from_page_id", None):
        style_samples = fetch_style_samples(notion_client, args.style_from_page_id)
    style_note = getattr(args, "style_note", "") or ""
    reader = _resolve_reader_for_llm_refine(notion_client, args, page_id=page_id)
    system_prompt = (
        build_llm_refine_system_prompt(style_samples, style_note, reader=reader)
        + WHOLE_PAGE_CROSS_SECTION_DIRECTIVE
    )

    prompt_parts: List[str] = [
        f"词条：{title or '(未命名)'}",
        "",
        "该页将被整体重写。为下列每个 section 产出新内容，保证彼此不重复且各司其职。",
        "",
    ]
    for s, _hb, _body, current in section_info:
        role = LLM_SECTION_ROLE_GUIDANCE.get(s, "")
        list_spec = LLM_LIST_SECTION_SPEC.get(s)
        prompt_parts.append(f"### section: {s}")
        if role:
            prompt_parts.append(f"职责：{role}")
        if list_spec:
            prompt_parts.append(
                f"输出类型：list（{list_spec['min_items']}-{list_spec['max_items']} 条，"
                f"每条 {list_spec['max_chars_per_item']} 字以内）"
            )
        else:
            prompt_parts.append("输出类型：paragraph（可多段，段间用 \\n\\n）")
        prompt_parts.append("")
        prompt_parts.append("当前段内容：")
        prompt_parts.append("---")
        prompt_parts.append(current or "(空)")
        prompt_parts.append("---")
        prompt_parts.append("")

    if source_context:
        prompt_parts.extend(
            [
                "源页 / 相邻材料（供判据和张力参考）：",
                "---",
                source_context,
                "---",
                "",
            ]
        )

    example = {
        "定义": {"content": "...\n\n..."},
        "关键机制": {"items": ["...", "..."]},
        "核心判断": {"content": "..."},
    }
    prompt_parts.extend(
        [
            "",
            "## 输出格式（必须严格遵守）",
            "",
            "输出必须是且仅是一个 JSON 对象，key 是上面的 section 名，value 结构：",
            '- paragraph 段：{"content": "多段正文，\\n\\n 分段"}',
            '- list 段：{"items": ["第1条", "第2条", ...]}',
            "",
            "示例：",
            json.dumps(example, ensure_ascii=False, indent=2),
            "",
            "严禁：markdown 代码块、前言、解释、多余文字、省略号。只输出完整 JSON。",
        ]
    )
    user_prompt = "\n".join(prompt_parts)

    response = deepseek_client.chat(
        system=system_prompt,
        user=user_prompt,
        max_tokens=getattr(args, "max_tokens", 16000),
        temperature=getattr(args, "temperature", 0.4),
    )
    choices = response.get("choices") or []
    if not choices:
        raise NotionError(f"DeepSeek returned no choices: {response}")
    message = choices[0].get("message", {})
    raw_output = (message.get("content") or "").strip()
    reasoning = message.get("reasoning_content") or ""
    usage = response.get("usage") or {}

    if not raw_output:
        raise NotionError(f"DeepSeek returned empty content; reasoning={reasoning[:200]!r}")

    parsed = parse_whole_page_response(raw_output)
    if parsed is None:
        raise NotionError(f"Failed to parse whole-page JSON response; head={raw_output[:400]!r}")

    if getattr(args, "preview", False):
        preview_payload = {
            "wiki_page_id": page_id,
            "sections_requested": [s for s, *_ in section_info],
            "sections_returned": list(parsed.keys()),
            "model": deepseek_client.model,
            "preview": True,
            "parsed": parsed,
            "reasoning": reasoning,
            "usage": usage,
        }
        print(json.dumps(audit_success("llm-refine-page", preview_payload), ensure_ascii=False, indent=2))
        return 0

    mention_map = parse_mention_map(getattr(args, "mention_map", None))
    link_style = getattr(args, "link_style", "link")
    results: List[Dict[str, Any]] = []
    for s, heading_block, body, _ in section_info:
        section_rewrite = parsed.get(s)
        if not isinstance(section_rewrite, dict):
            print(f"WARN: no rewrite for section {s!r} in LLM response", file=sys.stderr)
            continue
        deleted_ids: List[str] = []
        for b in body:
            bid = b.get("id")
            if not bid:
                continue
            try:
                notion_client.delete_block(bid)
                deleted_ids.append(bid)
            except NotionError as exc:
                print(f"WARN: failed to delete {bid}: {exc}", file=sys.stderr)
        new_blocks: List[Dict[str, Any]] = []
        render_mode = "paragraph"
        if "items" in section_rewrite and isinstance(section_rewrite["items"], list):
            render_mode = "bulleted_list_item"
            for item in section_rewrite["items"]:
                if not isinstance(item, str) or not item.strip():
                    continue
                for chunk in chunk_text(item.strip()):
                    rich = (
                        build_rich_text_with_mentions(chunk, mention_map, link_style)
                        if mention_map
                        else rich_text_value(chunk)
                    )
                    new_blocks.append(
                        {
                            "object": "block",
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {"rich_text": rich},
                        }
                    )
        elif "content" in section_rewrite and isinstance(section_rewrite["content"], str):
            content_text = section_rewrite["content"]
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content_text) if p.strip()]
            if not paragraphs:
                paragraphs = [content_text]
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
        else:
            print(f"WARN: section {s!r} rewrite has neither items nor content", file=sys.stderr)
            continue
        if new_blocks:
            notion_client.append_block_children(page_id, new_blocks, after=heading_block["id"])
        results.append(
            {
                "heading": s,
                "render_mode": render_mode,
                "deleted_block_count": len(deleted_ids),
                "new_block_count": len(new_blocks),
            }
        )

    payload = {
        "wiki_page_id": page_id,
        "model": deepseek_client.model,
        "section_count": len(results),
        "sections": results,
        "usage": usage,
    }
    log_path = append_jsonl_log(
        daily_log_filename("llm-refine-log.jsonl"),
        {
            **payload,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "raw_output": raw_output,
            "reasoning": reasoning,
        },
    )
    payload["log_path"] = str(log_path)
    print(json.dumps(audit_success("llm-refine-page", payload), ensure_ascii=False, indent=2))
    return 0


LLM_VALIDATION_SYSTEM_PROMPT = (
    "你是永久笔记的校对编辑。评估该段落对它所属词条（由 user prompt 给出的\"词条\"名判定主题）是否合格。\n"
    "\n"
    "你需要评估一段针对某个 heading 的永久笔记内容。评估标准：\n"
    "\n"
    "1. **有解读 vs 提要**：是否存在可被反驳的论断（非中性转述）；是否给出可操作判据（\"测试方式：...\"、\"判据是：...\"）；是否指出常见误解；是否有非显见洞察\n"
    "2. **段职责遵守**：是否在该 heading 的 role guidance 范围内，不越界写其他段的内容\n"
    "3. **类比质量**：类比是否精确；是否做了回溯映射（不只是抛一个类比就走）；是否指出类比不贴合的边界\n"
    "4. **风格合规**：是否避免了空洞形容词（关键/核心/重要）、营销话术（颠覆/革命/breakthrough）、玩具比喻（小明小红）\n"
    "5. **内在一致性**：段内论断是否前后一致；和源材料有无矛盾\n"
    "6. **跨领域污染（直接判 FAIL 的硬条件）**：段落是否硬拉不属于该词条主题的术语 / 框架 / 类比？典型场景：\n"
    "   - 量化 / 金融主题段落中出现 LangChain / ReAct / AgentExecutor / AutoGPT / ChatGPT prompt / tool use / agent loop\n"
    "   - 产品 / 设计主题段落中出现止损线 / 回测 / K 线 / 均线 / 因子\n"
    "   - 硬件 / 底层主题段落中出现 DevOps / 云厂商 API\n"
    "   命中此项 = 直接 pass=false, score ≤ 5，不管其他 5 项如何；issues 里必须把污染术语列出来。\n"
    "\n"
    "输出一个纯 JSON 对象（不要 markdown 代码块、不要前言），格式：\n"
    "{\n"
    "  \"pass\": true/false,\n"
    "  \"score\": 0-10 整数,\n"
    "  \"issues\": [\"具体问题 1\", \"具体问题 2\"],\n"
    "  \"strengths\": [\"亮点 1\", \"亮点 2\"],\n"
    "  \"suggestion\": \"一句具体改进建议\"\n"
    "}\n"
    "\n"
    "score 标准：10=标杆样本；8-9=合格有解读；6-7=部分合格，需小改；4-5=偏提要或越界，需重写；0-3=空洞或与源材料矛盾。\n"
    "pass=true 仅当 score >= 8。\n"
)


def command_llm_validate(
    notion_client: NotionClient,
    validator_client: LLMClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    page_id = args.page_id
    top_blocks = iterate_block_children(notion_client, page_id)
    database = notion_client.retrieve_database(wiki_database_id)
    page = notion_client.retrieve_page(page_id)
    title_prop = mapping.get("title_property") or detect_title_property(database)
    title = extract_title(page, title_prop) if title_prop else ""

    if args.heading:
        sections = [args.heading]
    else:
        sections = ["定义", "为什么重要", "关键机制", "核心判断", "实现信号", "与相邻概念的区别"]

    results: List[Dict[str, Any]] = []
    for s in sections:
        heading_block, body = find_section_body(top_blocks, s)
        if heading_block is None:
            continue
        current = "\n\n".join(
            extract_block_text(b) for b in body if extract_block_text(b).strip()
        ).strip()
        if not current:
            results.append({"heading": s, "pass": False, "score": 0, "note": "section empty"})
            continue
        role = LLM_SECTION_ROLE_GUIDANCE.get(s, "")
        list_spec = LLM_LIST_SECTION_SPEC.get(s)
        list_note = ""
        if list_spec:
            list_note = f"\n这是一个条目型 section，应为 {list_spec['min_items']}-{list_spec['max_items']} 条 bullet。"

        user_prompt = (
            f"词条：{title or '(未命名)'}\n"
            f"Heading：{s}\n"
            f"该段职责（role guidance）：\n{role}{list_note}\n\n"
            f"待评估段落内容：\n---\n{current}\n---\n\n"
            "请按系统提示的 5 项标准评估，输出 JSON。"
        )

        try:
            response = validator_client.chat(
                system=LLM_VALIDATION_SYSTEM_PROMPT,
                user=user_prompt,
                max_tokens=getattr(args, "max_tokens", 2000),
            )
        except NotionError as exc:
            results.append({"heading": s, "error": str(exc)})
            continue

        choices = response.get("choices") or []
        if not choices:
            results.append({"heading": s, "error": "no choices"})
            continue
        message = choices[0].get("message", {})
        raw_output = (message.get("content") or "").strip()
        if not raw_output:
            results.append({"heading": s, "error": "empty content"})
            continue

        parsed = None
        fence_match = re.match(r"```(?:json)?\s*(.*?)\s*```", raw_output, re.DOTALL)
        candidate = fence_match.group(1).strip() if fence_match else raw_output
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            obj_match = re.search(r"\{.*\}", candidate, re.DOTALL)
            if obj_match:
                try:
                    parsed = json.loads(obj_match.group(0))
                except (json.JSONDecodeError, ValueError):
                    pass
        if not isinstance(parsed, dict):
            results.append({"heading": s, "error": "parse failure", "raw": raw_output[:300]})
            continue

        results.append(
            {
                "heading": s,
                "pass": bool(parsed.get("pass")),
                "score": parsed.get("score"),
                "issues": parsed.get("issues", []),
                "strengths": parsed.get("strengths", []),
                "suggestion": parsed.get("suggestion", ""),
                "usage": response.get("usage", {}),
            }
        )

    pass_count = sum(1 for r in results if r.get("pass"))
    fail_count = sum(1 for r in results if r.get("pass") is False)
    error_count = sum(1 for r in results if r.get("error"))
    scored = [r.get("score") for r in results if isinstance(r.get("score"), int)]
    avg_score = round(sum(scored) / len(scored), 2) if scored else 0

    annotated_blocks_count = 0
    if getattr(args, "annotate", False):
        callout_blocks: List[Dict[str, Any]] = []
        intro_text = (
            f"DeepSeek 校验 · {today_iso_date()} · {validator_client.provider}/{validator_client.model}\n"
            f"平均分 {avg_score}/10 · pass={pass_count}, fail={fail_count}, error={error_count}"
        )
        callout_blocks.append(
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"emoji": "🔍"},
                    "rich_text": rich_text_value(intro_text),
                },
            }
        )
        for r in results:
            if r.get("error"):
                text = f"[{r['heading']}] 校验失败：{r['error']}"
            else:
                status = "PASS" if r.get("pass") else "FAIL"
                score = r.get("score", "?")
                issues = " / ".join(r.get("issues") or [])
                suggestion = r.get("suggestion") or ""
                lines = [
                    f"[{r['heading']}] · {status} · {score}/10",
                ]
                if issues:
                    lines.append(f"问题：{issues}")
                if suggestion:
                    lines.append(f"建议：{suggestion}")
                text = "\n".join(lines)
            emoji = "✅" if r.get("pass") else ("❌" if r.get("pass") is False else "⚠️")
            callout_blocks.append(
                {
                    "object": "block",
                    "type": "callout",
                    "callout": {
                        "icon": {"emoji": emoji},
                        "rich_text": rich_text_value(text),
                    },
                }
            )
        if not getattr(args, "dry_run", False):
            notion_client.append_block_children(page_id, callout_blocks)
        annotated_blocks_count = len(callout_blocks)

    payload = {
        "wiki_page_id": page_id,
        "validator_provider": validator_client.provider,
        "validator_model": validator_client.model,
        "sections_checked": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "error_count": error_count,
        "avg_score": avg_score,
        "annotated_blocks_count": annotated_blocks_count,
        "results": results,
    }
    print(json.dumps(audit_success("llm-validate", payload), ensure_ascii=False, indent=2))
    return 0 if fail_count == 0 and error_count == 0 else 1


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

    # Wrapper-style headings that should appear at most once per page. If multiple
    # copies exist (from earlier pre-dedup-fix compile runs), keep the FIRST and
    # remove later duplicates — the first copy is the one llm-refine-page wrote
    # clean content under; later copies carry heuristic bootstrap bodies that
    # never got refined.
    unique_wrapper_headings = ("结构化整理", "补充整理")

    # Partition blocks into {append_heading_prefix} sections for content-based dedup,
    # plus identify wrapper-heading duplicates separately.
    sections: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    current: Optional[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = None
    wrapper_first_seen: Dict[str, int] = {}  # prefix → first heading idx
    wrapper_duplicate_ranges: List[Tuple[int, int]] = []  # [start, end_exclusive)
    wrapper_start_idx: Optional[int] = None
    wrapper_current_prefix: Optional[str] = None

    def finalize_wrapper_range(end_idx: int):
        nonlocal wrapper_start_idx, wrapper_current_prefix
        if wrapper_start_idx is not None and wrapper_current_prefix is not None:
            first_idx = wrapper_first_seen.get(wrapper_current_prefix)
            if first_idx is not None and first_idx != wrapper_start_idx:
                wrapper_duplicate_ranges.append((wrapper_start_idx, end_idx))
        wrapper_start_idx = None
        wrapper_current_prefix = None

    for bi, block in enumerate(blocks):
        heading_text = ""
        if block.get("type") == "heading_2":
            heading_text = rich_text_plain_text(block.get("heading_2", {}).get("rich_text", []))
        # Track wrapper-heading ranges (for 结构化整理 / 补充整理 dedup)
        matched_wrapper = next((p for p in unique_wrapper_headings if heading_text.startswith(p)), None)
        if matched_wrapper is not None:
            finalize_wrapper_range(bi)
            wrapper_start_idx = bi
            wrapper_current_prefix = matched_wrapper
            if matched_wrapper not in wrapper_first_seen:
                wrapper_first_seen[matched_wrapper] = bi
        elif heading_text.startswith(append_heading_prefix):
            # new 增量更新 heading closes any open wrapper
            finalize_wrapper_range(bi)
        # Partition for 增量更新-prefix content dedup (unchanged logic)
        if heading_text.startswith(append_heading_prefix):
            if current is not None:
                sections.append(current)
            current = (block, [])
        elif current is not None:
            current[1].append(block)
    finalize_wrapper_range(len(blocks))
    if current is not None:
        sections.append(current)

    # Key each 增量更新 section by its first non-empty paragraph text (the raw
    # excerpt — canonical identifier). Earliest occurrence wins; later sections
    # with matching key get deleted entirely (heading + body). This works even
    # when later sections have extra heuristic cruft appended: the raw excerpt
    # paragraph is the anchor, not the full body concatenation.
    def section_key(body_blocks: List[Dict[str, Any]]) -> str:
        for b in body_blocks:
            btype = b.get("type")
            if btype == "heading_2":
                # stop at next wrapper heading — don't fold it into the key
                break
            if btype in ("paragraph", "quote", "bulleted_list_item", "numbered_list_item"):
                t = extract_block_text(b).strip()
                if t:
                    return t[:500]
        return ""

    # Group sections by key first; for each group pick the "richest" one as
    # the survivor. Ranking: (a) sections whose body contains a 结构化整理 or
    # 补充整理 wrapper win over bare raw-only sections (they hold Kimi refined
    # content); (b) among equally-refined, more body blocks wins; (c) earliest
    # wins as tiebreaker. Without this, 保留 earliest would drop Kimi content
    # when the first section was a bare raw excerpt and a later section
    # contained the wrappers (observed on Pop Mart page during development).
    def section_richness_rank(body_blocks: List[Dict[str, Any]]) -> Tuple[int, int, int]:
        has_refined_wrapper = 0
        for b in body_blocks:
            if b.get("type") == "heading_2":
                txt = rich_text_plain_text(b.get("heading_2", {}).get("rich_text", []))
                if any(txt.startswith(p) for p in ("结构化整理", "补充整理")):
                    has_refined_wrapper = 1
                    break
        return (has_refined_wrapper, len(body_blocks), 0)

    key_groups: Dict[str, List[int]] = {}
    keyless_indices: List[int] = []
    section_keys: List[str] = []
    for idx, (_heading, body) in enumerate(sections):
        key = section_key(body)
        section_keys.append(key)
        if not key:
            keyless_indices.append(idx)
            continue
        key_groups.setdefault(key, []).append(idx)

    to_delete: List[Dict[str, Any]] = []
    kept_indices: List[int] = list(keyless_indices)
    for key, group_indices in key_groups.items():
        if len(group_indices) == 1:
            kept_indices.append(group_indices[0])
            continue
        # Pick the survivor by richness; break ties by earliest (smallest idx)
        best_idx = max(
            group_indices,
            key=lambda i: section_richness_rank(sections[i][1]) + (-i,),
        )
        kept_indices.append(best_idx)
        for i in group_indices:
            if i == best_idx:
                continue
            heading, body = sections[i]
            to_delete.append(heading)
            to_delete.extend(body)

    # Collect wrapper-heading duplicate block ranges into to_delete
    wrapper_blocks_removed = 0
    for start, end in wrapper_duplicate_ranges:
        for block in blocks[start:end]:
            to_delete.append(block)
            wrapper_blocks_removed += 1

    callouts_to_delete: List[Dict[str, Any]] = []
    if getattr(args, "drop_validator_callouts", False):
        callouts_to_delete = _collect_validator_callouts(blocks)

    # De-duplicate block IDs before delete — a block can be flagged by BOTH
    # the section dedup path and the wrapper-duplicate path (when a duplicate
    # 增量更新 section wraps a duplicate 补充整理 wrapper). Collect unique ids
    # in insertion order so audit output stays stable.
    seen_ids: set = set()
    unique_to_delete: List[Dict[str, Any]] = []
    for block in to_delete + callouts_to_delete:
        bid = block.get("id")
        if not bid or bid in seen_ids:
            continue
        seen_ids.add(bid)
        unique_to_delete.append(block)

    deleted_ids: List[str] = []
    for block in unique_to_delete:
        block_id = block["id"]
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
        "section_duplicates_flagged": len(to_delete),
        "wrapper_duplicates_flagged": wrapper_blocks_removed,
        "callouts_flagged": len(callouts_to_delete),
        "removed_block_ids": deleted_ids,
    }
    print(json.dumps(audit_success("cleanup-wiki-page", payload), ensure_ascii=False, indent=2))
    return 0


DECISION_SOURCES = {"editorial", "audit", "verification", "failures"}
DECISION_STATUSES = {"open", "in_review", "resolved", "dropped"}


def decisions_log_path() -> Path:
    return ensure_raw_dumps_dir() / "decisions.jsonl"


def build_decision_id(source: str, subject_page_id: Optional[str], trigger_key: str) -> str:
    payload = f"{source}::{subject_page_id or ''}::{trigger_key}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def load_decisions_state() -> Dict[str, Dict[str, Any]]:
    path = decisions_log_path()
    if not path.exists():
        return {}
    state: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = record.get("id")
            if rid:
                state[rid] = record
    return state


def append_decision_record(record: Dict[str, Any]) -> Path:
    path = decisions_log_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def render_editorial_issues(issues: List[Dict[str, Any]]) -> List[str]:
    rendered: List[str] = []
    for issue in issues:
        check = issue.get("check", "")
        if check == "required_property_empty":
            rendered.append(f"empty:{issue.get('property')}")
        elif check == "required_property_missing_in_schema":
            rendered.append(f"schema_missing:{issue.get('property')}")
        elif check == "missing_heading":
            rendered.append(f"missing_heading:{issue.get('heading')}")
        elif check == "too_many_evidence_items":
            rendered.append(f"evidence_over_limit:{issue.get('count')}")
        elif check == "duplicate_update_sections":
            rendered.append(f"dup_updates:{issue.get('count')}")
        elif check == "title_not_normalized":
            rendered.append("title_not_normalized")
        elif check == "title_contains_delimiter":
            rendered.append("title_has_delimiter")
        else:
            rendered.append(check or "unknown")
    return rendered


def collect_editorial_signals(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    limit: int = 50,
) -> List[Dict[str, Any]]:
    pages = query_database_pages(client, wiki_database_id, None, page_size=20, max_pages=5)
    if limit:
        pages = pages[:limit]
    signals: List[Dict[str, Any]] = []
    for page in pages:
        page_id = page.get("id")
        if not page_id:
            continue
        try:
            result = check_editorial_compliance(client, wiki_database_id, mapping, page_id)
        except NotionError:
            continue
        compliance = result.get("compliance")
        if compliance not in ("yellow", "red"):
            continue
        rendered = render_editorial_issues(result.get("issues") or [])
        trigger_key = f"{compliance}:{'|'.join(sorted(rendered))[:200]}"
        signals.append({
            "source": f"editorial_{compliance}",
            "subject_page_id": page_id,
            "trigger": f"check-editorial {compliance}: {', '.join(rendered[:3])}" if rendered else f"check-editorial {compliance}",
            "trigger_key": trigger_key,
            "evidence": {
                "compliance": compliance,
                "title": result.get("title"),
                "issues": rendered,
            },
        })
    return signals


def iter_recent_audit_records(days: int = 7) -> List[Dict[str, Any]]:
    dump_dir = ensure_raw_dumps_dir()
    today = dt.datetime.now(dt.timezone.utc).date()
    records: List[Dict[str, Any]] = []
    for delta in range(max(1, days)):
        d = today - dt.timedelta(days=delta)
        path = dump_dir / f"{d.strftime('%Y-%m-%d')}-audit-log.jsonl"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def collect_audit_review_required(days: int = 7) -> List[Dict[str, Any]]:
    seen_keys: set = set()
    signals: List[Dict[str, Any]] = []
    for record in iter_recent_audit_records(days=days):
        if not record.get("review_required"):
            continue
        page_id = record.get("page_id") or record.get("wiki_page_id")
        raw_page_id = record.get("raw_page_id") or ""
        strategy = record.get("match_strategy", "unknown")
        trigger_key = f"{strategy}:{page_id or ''}:{raw_page_id}"
        if trigger_key in seen_keys:
            continue
        seen_keys.add(trigger_key)
        signals.append({
            "source": "audit_review_required",
            "subject_page_id": page_id,
            "trigger": f"compile {strategy} hit flagged review_required",
            "trigger_key": trigger_key,
            "evidence": {
                "match_strategy": strategy,
                "raw_page_id": raw_page_id or None,
                "command": record.get("command"),
                "last_seen": record.get("timestamp"),
            },
        })
    return signals


def collect_verification_needs_review(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
) -> List[Dict[str, Any]]:
    database = client.retrieve_database(wiki_database_id)
    title_prop = mapping.get("title_property") or detect_title_property(database) or "Name"
    verification_prop = mapping.get("verification_property") or "Verification"
    prop_meta = database.get("properties", {}).get(verification_prop)
    if not prop_meta:
        return []
    prop_type = prop_meta.get("type")
    if prop_type == "status":
        filter_body = {"property": verification_prop, "status": {"equals": "Needs Review"}}
    elif prop_type == "select":
        filter_body = {"property": verification_prop, "select": {"equals": "Needs Review"}}
    else:
        return []
    try:
        pages = query_database_pages(client, wiki_database_id, filter_body)
    except NotionError:
        return []
    signals: List[Dict[str, Any]] = []
    for page in pages:
        page_id = page.get("id")
        if not page_id:
            continue
        title = extract_title(page, title_prop) if title_prop else ""
        signals.append({
            "source": "verification_needs_review",
            "subject_page_id": page_id,
            "trigger": f"Verification = Needs Review: {title}" if title else "Verification = Needs Review",
            "trigger_key": f"needs_review:{page_id}",
            "evidence": {"title": title, "verification_property": verification_prop},
        })
    return signals


def collect_compile_failures(days: int = 7) -> List[Dict[str, Any]]:
    seen_keys: set = set()
    signals: List[Dict[str, Any]] = []
    for record in iter_recent_audit_records(days=days):
        if record.get("status") != "error":
            continue
        command = record.get("command", "") or ""
        if not command.startswith("compile-"):
            continue
        raw_page_id = record.get("raw_page_id") or ""
        error = record.get("error", "") or ""
        trigger_key = f"{command}:{raw_page_id}:{error[:100]}"
        if trigger_key in seen_keys:
            continue
        seen_keys.add(trigger_key)
        signals.append({
            "source": "compile_failure",
            "subject_page_id": None,
            "trigger": f"{command} failed: {error[:80]}",
            "trigger_key": trigger_key,
            "evidence": {
                "command": command,
                "raw_page_id": raw_page_id or None,
                "error": error,
                "last_seen": record.get("timestamp"),
            },
        })
    return signals


def command_list_review_queue(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    source_arg = (args.source or "all").strip()
    if source_arg == "all":
        requested = set(DECISION_SOURCES)
    else:
        requested = {s.strip() for s in source_arg.split(",") if s.strip()}
        invalid = requested - DECISION_SOURCES
        if invalid:
            raise NotionError(f"Unknown source(s): {sorted(invalid)}; allowed: {sorted(DECISION_SOURCES)}")

    signals: List[Dict[str, Any]] = []
    if "editorial" in requested:
        signals.extend(collect_editorial_signals(client, wiki_database_id, mapping, limit=args.editorial_limit))
    if "audit" in requested:
        signals.extend(collect_audit_review_required(days=args.days))
    if "verification" in requested:
        signals.extend(collect_verification_needs_review(client, wiki_database_id, mapping))
    if "failures" in requested:
        signals.extend(collect_compile_failures(days=args.days))

    state = load_decisions_state()
    new_decisions: List[Dict[str, Any]] = []
    existing_open: List[Dict[str, Any]] = []
    skipped_resolved: List[Dict[str, Any]] = []

    for signal in signals:
        did = build_decision_id(signal["source"], signal["subject_page_id"], signal["trigger_key"])
        existing = state.get(did)
        if existing:
            status = existing.get("status")
            if status in ("resolved", "dropped"):
                skipped_resolved.append({"id": did, "status": status, "trigger": signal["trigger"]})
                continue
            existing_open.append({"id": did, "status": status, "trigger": signal["trigger"], "subject_page_id": signal["subject_page_id"]})
            continue
        record = {
            "timestamp": iso_now(),
            "id": did,
            "event_type": "raised",
            "source": signal["source"],
            "subject_page_id": signal["subject_page_id"],
            "trigger": signal["trigger"],
            "evidence": signal["evidence"],
            "status": "open",
            "resolver": None,
            "rationale": None,
        }
        new_decisions.append(record)

    summary: Dict[str, Any] = {
        "sources_checked": sorted(requested),
        "signal_count": len(signals),
        "new_decision_count": len(new_decisions),
        "existing_open_count": len(existing_open),
        "skipped_resolved_count": len(skipped_resolved),
        "new": new_decisions,
        "existing_open": existing_open,
        "skipped_resolved": skipped_resolved,
    }

    if args.emit_decisions and not args.dry_run:
        for record in new_decisions:
            append_decision_record(record)
        summary["emitted"] = True
        summary["log_path"] = str(decisions_log_path())
    else:
        summary["emitted"] = False

    print(json.dumps(audit_success("list-review-queue", summary), ensure_ascii=False, indent=2))
    return 0


def command_resolve_decision(args: argparse.Namespace) -> int:
    if args.status not in DECISION_STATUSES:
        raise NotionError(f"--status must be one of {sorted(DECISION_STATUSES)}")
    if args.status == "open":
        raise NotionError("--status cannot be 'open' (decisions start as 'open' via list-review-queue --emit-decisions)")
    state = load_decisions_state()
    existing = state.get(args.id)
    if not existing:
        raise NotionError(f"Decision id not found in decisions.jsonl: {args.id}")
    prior_status = existing.get("status")
    if prior_status == args.status:
        raise NotionError(f"Decision {args.id} already in status '{args.status}'")
    record = {
        "timestamp": iso_now(),
        "id": args.id,
        "event_type": "resolved",
        "source": existing.get("source"),
        "subject_page_id": existing.get("subject_page_id"),
        "trigger": existing.get("trigger"),
        "status": args.status,
        "resolver": args.resolver or "session-layer",
        "rationale": args.rationale or "",
        "prior_status": prior_status,
    }
    log_path = append_decision_record(record)
    record["log_path"] = str(log_path)
    print(json.dumps(audit_success("resolve-decision", record), ensure_ascii=False, indent=2))
    return 0


# =========================================================================
# v18 P1 · 对象生命周期状态机 (Lifecycle) — growing / stable / stale / conflicted
# v18 P2 · 统一质量状态 (Quality)            — draft / review_required / validated / ready
# =========================================================================

LIFECYCLE_STATES = ("growing", "stable", "stale", "conflicted")
QUALITY_STATES = ("draft", "review_required", "validated", "ready")


def ensure_select_property(
    client: NotionClient,
    wiki_database_id: str,
    prop_name: str,
    options: Tuple[str, ...],
    color_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Create a select property on the Wiki DB if absent, with the given options.

    Idempotent: if property exists with correct type, no-op.  If exists as a
    non-select type, raises.  If exists but missing options, patches them in."""
    db = client.retrieve_database(wiki_database_id)
    existing = db.get("properties", {}).get(prop_name)
    default_color = color_map or {}
    if existing:
        if existing.get("type") != "select":
            raise NotionError(
                f"property {prop_name!r} exists but type is {existing.get('type')!r}, expected 'select'"
            )
        current_options = {o.get("name") for o in existing.get("select", {}).get("options", [])}
        missing = [o for o in options if o not in current_options]
        if not missing:
            return {"action": "already_exists", "property": prop_name}
        merged = list(existing.get("select", {}).get("options", [])) + [
            {"name": o, "color": default_color.get(o, "default")} for o in missing
        ]
        client.update_database(
            wiki_database_id,
            {"properties": {prop_name: {"select": {"options": merged}}}},
        )
        return {"action": "options_extended", "property": prop_name, "added": missing}
    payload = {
        "properties": {
            prop_name: {
                "select": {
                    "options": [
                        {"name": o, "color": default_color.get(o, "default")} for o in options
                    ]
                }
            }
        }
    }
    client.update_database(wiki_database_id, payload)
    return {"action": "created", "property": prop_name, "options": list(options)}


def find_latest_llm_validate_for_page(wiki_page_id: str, days: int = 30) -> Optional[Dict[str, Any]]:
    """Scan recent audit-log.jsonl for the most recent llm-validate entry whose
    wiki_page_id matches. Returns the full audit record or None."""
    records = iter_recent_audit_records(days=days)
    candidates = []
    norm = normalize_notion_id(wiki_page_id)
    for r in records:
        if r.get("command") != "llm-validate":
            continue
        if normalize_notion_id(r.get("wiki_page_id", "")) != norm:
            continue
        candidates.append(r)
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return candidates[0]


def find_recent_conflicts_for_page(wiki_page_id: str, days: int = 7) -> List[Dict[str, Any]]:
    """Compile records for this wiki page that emitted a diff (conflict signal)."""
    norm = normalize_notion_id(wiki_page_id)
    hits = []
    for r in iter_recent_audit_records(days=days):
        cmd = r.get("command", "")
        if not cmd.startswith("compile-"):
            continue
        wiki_block = r.get("wiki") or {}
        if normalize_notion_id(wiki_block.get("page_id", "")) != norm:
            continue
        if r.get("diff_appended"):
            hits.append(r)
    return hits


def open_decisions_for_page(wiki_page_id: str) -> List[Dict[str, Any]]:
    """Decisions whose subject_page_id matches and whose latest status is not terminal."""
    state = load_decisions_state()
    norm = normalize_notion_id(wiki_page_id)
    out = []
    for rec in state.values():
        if normalize_notion_id(rec.get("subject_page_id", "") or "") != norm:
            continue
        if rec.get("status") in ("open", "in_review"):
            out.append(rec)
    return out


def compute_lifecycle_state(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    wiki_page_id: str,
) -> Dict[str, Any]:
    page = client.retrieve_page(wiki_page_id)
    props = page.get("properties", {})

    level_prop = mapping.get("compounded_level_property") or "Compounded Level"
    level = props.get(level_prop, {}).get("number") or 0

    last_prop = mapping.get("last_compounded_at_property") or "Last Compounded At"
    last_date_str = (props.get(last_prop, {}).get("date") or {}).get("start", "") or ""
    last_date: Optional[dt.date] = None
    try:
        last_date = dt.date.fromisoformat(last_date_str[:10]) if last_date_str else None
    except ValueError:
        last_date = None
    today = dt.date.today()
    days_since = (today - last_date).days if last_date else 9999

    source_prop = mapping.get("source_property") or "Source"
    source_count = len(props.get(source_prop, {}).get("relation", []) or [])

    conflicts = find_recent_conflicts_for_page(wiki_page_id, days=7)

    editorial = check_editorial_compliance(client, wiki_database_id, mapping, wiki_page_id)
    compliance = editorial.get("compliance")

    if conflicts:
        state = "conflicted"
    elif compliance == "green" and level >= 3 and days_since >= 7:
        state = "stable"
    elif days_since >= 30 and compliance not in ("green", "placeholder"):
        state = "stale"
    else:
        state = "growing"

    return {
        "wiki_page_id": wiki_page_id,
        "lifecycle": state,
        "evidence": {
            "compounded_level": level,
            "last_compounded_at": last_date_str or None,
            "days_since_compound": days_since if last_date else None,
            "source_count": source_count,
            "compliance": compliance,
            "recent_conflicts": len(conflicts),
        },
    }


def compute_quality_state(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    wiki_page_id: str,
) -> Dict[str, Any]:
    editorial = check_editorial_compliance(client, wiki_database_id, mapping, wiki_page_id)
    compliance = editorial.get("compliance")

    latest_validate = find_latest_llm_validate_for_page(wiki_page_id, days=30)
    avg_score = latest_validate.get("avg_score") if latest_validate else None
    fail_count = latest_validate.get("fail_count") if latest_validate else None

    open_decisions = open_decisions_for_page(wiki_page_id)

    if compliance == "placeholder":
        state = "draft"
    elif compliance == "red":
        state = "draft"
    elif latest_validate is None:
        # editorial yellow/green but never validated
        state = "draft"
    elif fail_count and fail_count > 0:
        state = "review_required"
    elif compliance == "yellow":
        state = "review_required"
    elif open_decisions:
        state = "review_required"
    elif compliance == "green" and isinstance(avg_score, (int, float)) and avg_score >= 8:
        # ready requires reference-check conformance; skip for MVP, default to validated.
        # Future: if caller passes a reference_page_id, compute conformance and upgrade to ready.
        state = "validated"
    else:
        state = "review_required"

    return {
        "wiki_page_id": wiki_page_id,
        "quality": state,
        "evidence": {
            "compliance": compliance,
            "editorial_issue_count": editorial.get("issue_count"),
            "latest_validate_avg_score": avg_score,
            "latest_validate_fail_count": fail_count,
            "latest_validate_timestamp": latest_validate.get("timestamp") if latest_validate else None,
            "open_decisions_count": len(open_decisions),
        },
    }


def _write_state_to_notion(
    client: NotionClient,
    wiki_page_id: str,
    prop_name: str,
    state_value: str,
) -> None:
    client.update_page(
        wiki_page_id,
        {"properties": {prop_name: {"select": {"name": state_value}}}},
    )


def _append_states_log(record: Dict[str, Any]) -> Path:
    return append_jsonl_log(daily_log_filename("states-log.jsonl"), record)


LIFECYCLE_COLOR_MAP = {"growing": "blue", "stable": "green", "stale": "gray", "conflicted": "red"}
QUALITY_COLOR_MAP = {"draft": "gray", "review_required": "yellow", "validated": "green", "ready": "purple"}


def command_compute_lifecycle_state(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    prop_name = getattr(args, "notion_property", None) or "Lifecycle"
    if args.write_notion:
        ensure_select_property(client, wiki_database_id, prop_name, LIFECYCLE_STATES, LIFECYCLE_COLOR_MAP)

    if args.all:
        pages = query_database_pages(client, wiki_database_id, None, page_size=20, max_pages=5)
        if args.limit:
            pages = pages[: args.limit]
        results: List[Dict[str, Any]] = []
        for p in pages:
            pid = p.get("id")
            if not pid:
                continue
            try:
                r = compute_lifecycle_state(client, wiki_database_id, mapping, pid)
            except NotionError as exc:
                results.append({"wiki_page_id": pid, "error": str(exc)})
                continue
            r["timestamp"] = iso_now()
            _append_states_log({**r, "kind": "lifecycle"})
            if args.write_notion:
                try:
                    _write_state_to_notion(client, pid, prop_name, r["lifecycle"])
                    r["notion_written"] = True
                except NotionError as exc:
                    r["notion_write_error"] = str(exc)
            results.append(r)
        summary = {
            "scope": "all",
            "count": len(results),
            "by_state": {s: sum(1 for r in results if r.get("lifecycle") == s) for s in LIFECYCLE_STATES},
            "results": results,
        }
        print(json.dumps(audit_success("compute-lifecycle-state", summary), ensure_ascii=False, indent=2))
        return 0

    if not args.page_id:
        raise NotionError("compute-lifecycle-state requires either <page_id> or --all")
    r = compute_lifecycle_state(client, wiki_database_id, mapping, args.page_id)
    r["timestamp"] = iso_now()
    _append_states_log({**r, "kind": "lifecycle"})
    if args.write_notion:
        try:
            _write_state_to_notion(client, args.page_id, prop_name, r["lifecycle"])
            r["notion_written"] = True
        except NotionError as exc:
            r["notion_write_error"] = str(exc)
    print(json.dumps(audit_success("compute-lifecycle-state", r), ensure_ascii=False, indent=2))
    return 0


def command_compute_quality_state(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    prop_name = getattr(args, "notion_property", None) or "Quality"
    if args.write_notion:
        ensure_select_property(client, wiki_database_id, prop_name, QUALITY_STATES, QUALITY_COLOR_MAP)

    if args.all:
        pages = query_database_pages(client, wiki_database_id, None, page_size=20, max_pages=5)
        if args.limit:
            pages = pages[: args.limit]
        results: List[Dict[str, Any]] = []
        for p in pages:
            pid = p.get("id")
            if not pid:
                continue
            try:
                r = compute_quality_state(client, wiki_database_id, mapping, pid)
            except NotionError as exc:
                results.append({"wiki_page_id": pid, "error": str(exc)})
                continue
            r["timestamp"] = iso_now()
            _append_states_log({**r, "kind": "quality"})
            if args.write_notion:
                try:
                    _write_state_to_notion(client, pid, prop_name, r["quality"])
                    r["notion_written"] = True
                except NotionError as exc:
                    r["notion_write_error"] = str(exc)
            results.append(r)
        summary = {
            "scope": "all",
            "count": len(results),
            "by_state": {s: sum(1 for r in results if r.get("quality") == s) for s in QUALITY_STATES},
            "results": results,
        }
        print(json.dumps(audit_success("compute-quality-state", summary), ensure_ascii=False, indent=2))
        return 0

    if not args.page_id:
        raise NotionError("compute-quality-state requires either <page_id> or --all")
    r = compute_quality_state(client, wiki_database_id, mapping, args.page_id)
    r["timestamp"] = iso_now()
    _append_states_log({**r, "kind": "quality"})
    if args.write_notion:
        try:
            _write_state_to_notion(client, args.page_id, prop_name, r["quality"])
            r["notion_written"] = True
        except NotionError as exc:
            r["notion_write_error"] = str(exc)
    print(json.dumps(audit_success("compute-quality-state", r), ensure_ascii=False, indent=2))
    return 0


GEMINI_ARBITER_SYSTEM_PROMPT = (
    "你是永久笔记质量的第三方仲裁员。你将看到一段 Kimi 产出的永久笔记 heading 内容，以及 DeepSeek 对它的 FAIL 校验意见。\n"
    "\n"
    "任务：判 DeepSeek 的 FAIL 是否成立。\n"
    "- DeepSeek 指出的问题是否真实、严重？\n"
    "- Kimi 的产出是否真的需要重写（而不是小修就可用）？\n"
    "\n"
    "评判原则：\n"
    "- 不要无脑附和 DeepSeek；如果它提的是鸡蛋里挑骨头 / 过度苛刻 / 个人风格偏好差异，应推翻 FAIL（uphold_fail=false）。\n"
    "- 也不要为了推翻而推翻；如果 Kimi 产出确实空洞、缺解读、越界、与事实矛盾，该维持 FAIL 就维持（uphold_fail=true）。\n"
    "- 阈值：只有当 Kimi 确实需要整段重写才 uphold；小修建议（加一句、改个词）不应 uphold。\n"
    "\n"
    "输出必须是且仅是一个 JSON（不要 markdown、不要前言）：\n"
    '{\n'
    '  "uphold_fail": true/false,\n'
    '  "reasoning": "一两句话说明判据"\n'
    '}\n'
)


def parse_gemini_json(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    raw = raw.strip()
    fence = re.match(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    candidate = fence.group(1).strip() if fence else raw
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        obj_match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not obj_match:
            return None
        try:
            parsed = json.loads(obj_match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    return parsed if isinstance(parsed, dict) else None


# ─── Judge-role LLM (Phase 1: deepseek-chat cheap classifier) ──────────────

def _append_judge_log(record: Dict[str, Any]) -> Path:
    return append_jsonl_log(daily_log_filename("judge-log.jsonl"), record)


def judge_chat(
    env: Dict[str, str],
    system_rules: str,
    user_prompt: str,
    choices: Tuple[str, ...],
    context_tag: str = "",
    max_tokens: int = 500,
) -> Dict[str, Any]:
    """Cheap categorical LLM classifier (deepseek-chat).

    Returns {"choice": str, "reasoning": str, "confidence": float, "error": Optional[str]}.
    choice is guaranteed to be one of `choices` (falls back to choices[-1], typically
    'uncertain', on any parse / API failure). confidence is 0.0 on failure.
    Every call is logged to raw/notion_dumps/YYYY-MM-DD-judge-log.jsonl for audit.
    """
    if not choices:
        raise NotionError("judge_chat requires at least one choice")

    fallback = choices[-1]
    enriched_system = (
        system_rules
        + "\n\n## 输出格式（严格）\n"
        + "仅输出一个 JSON 对象：\n"
        + '{"choice": "<one of options>", "reasoning": "<一句理由>", "confidence": <0.0-1.0>}\n'
        + f"\noptions = {list(choices)}\n"
        + "confidence: 0.0 完全不确定 / 0.5 一般 / 1.0 非常确定。\n"
        + "严禁 markdown 代码块、前言、多余文字。"
    )

    try:
        client = build_llm_client(env, "deepseek-chat", None)
    except NotionError as exc:
        record = {
            "timestamp": iso_now(),
            "tag": context_tag,
            "error": f"build_client_failed: {exc}",
            "choice": fallback,
            "confidence": 0.0,
        }
        _append_judge_log(record)
        return {"choice": fallback, "reasoning": f"judge_unavailable: {exc}", "confidence": 0.0, "error": str(exc)}

    t0 = time.time()
    try:
        response = client.chat(system=enriched_system, user=user_prompt, max_tokens=max_tokens, temperature=0.0)
    except NotionError as exc:
        record = {
            "timestamp": iso_now(),
            "tag": context_tag,
            "error": f"api_error: {exc}",
            "latency_s": round(time.time() - t0, 2),
            "choice": fallback,
            "confidence": 0.0,
        }
        _append_judge_log(record)
        return {"choice": fallback, "reasoning": f"judge_api_error: {exc}", "confidence": 0.0, "error": str(exc)}

    latency = time.time() - t0
    content = (response.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    parsed = parse_gemini_json(content)

    if not parsed or not isinstance(parsed, dict):
        choice, reasoning, confidence = fallback, f"parse_failed: head={content[:150]!r}", 0.0
    else:
        raw_choice = parsed.get("choice", "")
        if raw_choice not in choices:
            choice = fallback
            reasoning = f"invalid_choice {raw_choice!r} not in {list(choices)}"
            confidence = 0.0
        else:
            choice = raw_choice
            reasoning = str(parsed.get("reasoning", ""))
            try:
                confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
            except (ValueError, TypeError):
                confidence = 0.0

    record = {
        "timestamp": iso_now(),
        "tag": context_tag,
        "latency_s": round(latency, 2),
        "choices": list(choices),
        "choice": choice,
        "reasoning": reasoning,
        "confidence": confidence,
        "model": client.model,
        "usage": response.get("usage", {}),
        "raw_content_head": content[:300],
    }
    _append_judge_log(record)

    return {"choice": choice, "reasoning": reasoning, "confidence": confidence, "error": None}


def judge_fill_section(
    env: Dict[str, str],
    wiki_page_id: str,
    missing_heading: str,
    topic_title: str,
    current_body_head: str,
) -> Dict[str, Any]:
    """Phase 3: decide whether to auto-fill a missing required heading now.

    'fill' → page has enough material / context for Kimi to produce a non-trivial
    section; auto-generate + append.
    'skip' → page too thin (placeholder / only raw excerpt); wait for more
    material. Keeps yellow state rather than fabricate noise."""
    system_rules = (
        "你在决定一个 wiki 页面是否应该自动补某个缺失的 heading。判据：\n"
        "\n"
        "- fill：当前 body 有足够内容（定义已成型 / 核心判断清晰 / 或至少有 300 字有解读的内容），"
        "补这一段不会是空话；而且该段对整个词条结构是必要的。\n"
        "- skip：body 太薄（< 300 字 / 仍是 placeholder / 只有 raw 摘录没有解读）。"
        "此时补段只会生成注水 / 空泛内容；应保持 yellow，等材料充足后再补。\n"
        "\n"
        "重点：只有当 Kimi 能写出有解读、贴合本主题的内容时才 fill；宁缺勿滥。"
    )
    user_prompt = (
        f"词条：{topic_title}\n"
        f"缺失的 heading：{missing_heading}\n"
        f"\n"
        f"当前 body 前 600 字：\n"
        f"{(current_body_head or '')[:600]}\n"
        f"\n"
        f"应该 fill 还是 skip？"
    )
    return judge_chat(
        env,
        system_rules,
        user_prompt,
        choices=("fill", "skip"),
        context_tag=f"fill_section:{wiki_page_id}:{missing_heading}",
        max_tokens=400,
    )


def autofill_missing_sections(
    client: NotionClient,
    env: Optional[Dict[str, str]],
    wiki_database_id: str,
    mapping: Dict[str, Any],
    wiki_page_id: str,
    reader: Optional[str] = None,
    no_judge: bool = False,
    provider: str = "kimi",
) -> Dict[str, Any]:
    """Phase 3 core: check editorial, pick missing required headings worth
    filling (via judge), append empty heading_2 blocks, then delegate content
    generation to command_llm_refine_page for each."""
    editorial = check_editorial_compliance(client, wiki_database_id, mapping, wiki_page_id)
    missing: List[str] = []
    for issue in editorial.get("issues", []) or []:
        if issue.get("check") == "missing_heading":
            heading = issue.get("heading")
            if heading:
                missing.append(heading)
    if not missing:
        return {
            "wiki_page_id": wiki_page_id,
            "compliance_before": editorial.get("compliance"),
            "missing_count": 0,
            "filled": [],
            "skipped": [],
            "no_action_reason": "no_missing_required_heading",
        }

    priority = ["定义", "核心判断", "关联概念", "原文证据"]
    missing_sorted = sorted(missing, key=lambda h: priority.index(h) if h in priority else 999)

    # Read current body for judge context
    try:
        current_body = read_page_body_text(client, wiki_page_id)
    except NotionError:
        current_body = ""
    page = client.retrieve_page(wiki_page_id)
    title_prop = mapping.get("title_property") or "Name"
    topic_title = extract_title(page, title_prop) if title_prop else ""

    filled: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for heading in missing_sorted:
        # Judge: should we fill this one?
        judge_outcome: Optional[Dict[str, Any]] = None
        if not no_judge and env is not None:
            judge_outcome = judge_fill_section(env, wiki_page_id, heading, topic_title, current_body)
            if judge_outcome["choice"] == "skip":
                skipped.append({
                    "heading": heading,
                    "reason": "judge_skip",
                    "confidence": judge_outcome["confidence"],
                    "judge_reasoning": judge_outcome["reasoning"][:200],
                })
                continue

        # Append empty heading_2 block for this missing heading
        try:
            client.append_block_children(
                wiki_page_id,
                [
                    {
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {"rich_text": rich_text_value(heading)},
                    }
                ],
            )
        except NotionError as exc:
            skipped.append({"heading": heading, "reason": f"append_failed: {exc}"})
            continue

        # Kimi writes body for this new heading via llm-refine-page single-section mode
        refine_args = argparse.Namespace(
            page_id=wiki_page_id,
            sections=heading,
            provider=provider,
            model=None,
            source_page_id=None,
            style_from_page_id=None,
            style_note="",
            mention_map=None,
            link_style="link",
            preview=False,
            max_tokens=16000,
            temperature=0.4,
            reader=reader,
        )
        try:
            refine_llm = build_llm_client(env, provider, None) if env else None
            if refine_llm is None:
                skipped.append({"heading": heading, "reason": "no_env_for_kimi"})
                continue
            payload, exit_code = _capture_command_stdout_json(
                command_llm_refine_page,
                client,
                refine_llm,
                wiki_database_id,
                mapping,
                refine_args,
            )
            filled.append({
                "heading": heading,
                "section_count": payload.get("section_count"),
                "judge": (
                    {"choice": judge_outcome["choice"], "confidence": judge_outcome["confidence"]}
                    if judge_outcome
                    else None
                ),
            })
        except NotionError as exc:
            skipped.append({"heading": heading, "reason": f"kimi_refine_failed: {exc}"})

    # Re-run editorial to observe post-fill compliance
    try:
        editorial_after = check_editorial_compliance(client, wiki_database_id, mapping, wiki_page_id)
        compliance_after = editorial_after.get("compliance")
    except NotionError:
        compliance_after = None

    return {
        "wiki_page_id": wiki_page_id,
        "compliance_before": editorial.get("compliance"),
        "compliance_after": compliance_after,
        "missing_count": len(missing),
        "filled": filled,
        "skipped": skipped,
    }


def command_autofill_missing_sections(
    client: NotionClient,
    env: Dict[str, str],
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    payload = autofill_missing_sections(
        client,
        env,
        wiki_database_id,
        mapping,
        args.page_id,
        reader=getattr(args, "reader", None),
        no_judge=getattr(args, "no_judge", False),
        provider=getattr(args, "provider", "kimi"),
    )
    print(json.dumps(audit_success("autofill-missing-sections", payload), ensure_ascii=False, indent=2))
    if payload.get("filled"):
        return 0
    return 0 if payload.get("missing_count", 0) == 0 else 1


def judge_alias_match(
    env: Dict[str, str],
    raw_title: str,
    raw_body: str,
    wiki_title: str,
    wiki_aliases: str,
    wiki_body: str,
    wiki_page_id: str,
) -> Dict[str, Any]:
    """Phase 2: decide if an alias-matched wiki candidate is the same entity
    as a new raw page. Used before compile commits to the alias-match update;
    'different_entity' causes compile to skip alias and treat as new-page."""
    system_rules = (
        "你是知识库的匹配判断助手。某条新 raw 材料的标题或别名，和一个 wiki 候选页碰撞了。判断两者是否同一实体。\n"
        "\n"
        "三个选项：\n"
        "- same_entity：同一对象，raw 是对这个 wiki 对象的追加 / 更新（术语 / 别名 overlap 且核心讨论方向一致）\n"
        "- different_entity：尽管标题或别名碰巧相同或相似，本质讨论的是不同对象（如同名异物、不同版本、不同领域）\n"
        "- uncertain：信息不足 / 边界模糊；保留 review_required 让人类决策\n"
        "\n"
        "评判要点：\n"
        "- 两者的**核心定义**是否一致？\n"
        "- **讨论领域**（行业 / 概念空间）是否重叠？\n"
        "- **对象粒度**是否相同？（对象 vs 对象的某个子系统，算不同）\n"
        "- 同名歧义（如 'Agent' 在 LLM 领域 vs 房产中介）= different_entity\n"
        "\n"
        "confidence 阈值建议：只有当你能说出具体理由时才 >= 0.8；否则给 uncertain。"
    )
    user_prompt = (
        f"【新 raw】\n"
        f"标题：{raw_title}\n"
        f"正文前 400 字：\n{(raw_body or '')[:400]}\n"
        f"\n"
        f"【候选 wiki 页】id={wiki_page_id}\n"
        f"标题：{wiki_title}\n"
        f"别名：{wiki_aliases or '(无)'}\n"
        f"正文前 400 字：\n{(wiki_body or '')[:400]}\n"
        f"\n"
        f"是否同一实体？"
    )
    return judge_chat(
        env,
        system_rules,
        user_prompt,
        choices=("same_entity", "different_entity", "uncertain"),
        context_tag=f"alias_match:{wiki_page_id}",
        max_tokens=500,
    )


def gemini_arbitrate(
    gemini_client: "LLMClient",
    wiki_title: str,
    heading: str,
    kimi_content: str,
    deepseek_result: Dict[str, Any],
) -> Dict[str, Any]:
    user_prompt = (
        f"词条：{wiki_title or '(未命名)'}\n"
        f"Heading：{heading}\n\n"
        "Kimi 产出：\n---\n"
        f"{kimi_content or '(空)'}\n"
        "---\n\n"
        "DeepSeek FAIL 意见：\n"
        f"- score: {deepseek_result.get('score')}\n"
        f"- issues: {deepseek_result.get('issues') or []}\n"
        f"- suggestion: {deepseek_result.get('suggestion') or ''}\n\n"
        "请仲裁。"
    )
    try:
        response = gemini_client.chat(
            system=GEMINI_ARBITER_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=2000,
        )
    except NotionError as exc:
        return {"uphold_fail": True, "reasoning": f"gemini_call_error: {exc}", "error": True}

    choices = response.get("choices") or []
    raw_output = ""
    if choices:
        raw_output = (choices[0].get("message", {}).get("content") or "").strip()

    parsed = parse_gemini_json(raw_output)
    if not parsed:
        return {
            "uphold_fail": True,
            "reasoning": f"gemini_parse_failed; default to uphold. raw={raw_output[:200]!r}",
            "error": True,
            "usage": response.get("usage", {}),
        }
    return {
        "uphold_fail": bool(parsed.get("uphold_fail", True)),
        "reasoning": str(parsed.get("reasoning", "")),
        "usage": response.get("usage", {}),
    }


def _capture_command_stdout_json(fn, *fn_args, **fn_kwargs) -> Tuple[Dict[str, Any], int]:
    """Run a command_* function, capture its stdout, parse as JSON.

    Commands like command_llm_refine_page / command_llm_validate end with a single
    print(json.dumps(audit_success(...))); this helper reuses them in pipeline
    flows without a big refactor. Side effects (Notion writes, logs) still happen.
    """
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = fn(*fn_args, **fn_kwargs)
    raw = buf.getvalue().strip()
    if not raw:
        return {}, exit_code
    try:
        return json.loads(raw), exit_code
    except json.JSONDecodeError:
        return {"raw_stdout_head": raw[:500]}, exit_code


def _build_compile_args_for_pipeline(
    raw_page_id: str,
    force: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        page_id=raw_page_id,
        title=None,
        canonical_id=None,
        verification=None,
        compounded_level=None,
        last_compounded_at=None,
        append_heading=None,
        increment_compounded_level=False,
        title_property=None,
        canonical_id_property=None,
        verification_property=None,
        compounded_level_property=None,
        last_compounded_at_property=None,
        raw_title_property=None,
        raw_source_url_property=None,
        raw_status_property=None,
        raw_processed_at_property=None,
        raw_target_wiki_page_property=None,
        raw_compiled_status=None,
        force=force,
        auto_refine=True,
        strict_alias=False,
        strict_fuzzy=False,
        emit_diff=False,
        merge_mode="append",
        replace_heading=None,
    )


def _build_refine_page_args_for_pipeline(
    wiki_page_id: str,
    provider: str,
    reader: Optional[str] = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        page_id=wiki_page_id,
        sections="",
        provider=provider,
        model=None,
        source_page_id=None,
        style_from_page_id=None,
        style_note="",
        mention_map=None,
        link_style="link",
        preview=False,
        max_tokens=16000,
        temperature=0.4,
        reader=reader,
    )


def _build_validate_args_for_pipeline(wiki_page_id: str, provider: str, annotate: bool) -> argparse.Namespace:
    return argparse.Namespace(
        page_id=wiki_page_id,
        heading=None,
        provider=provider,
        model=None,
        annotate=annotate,
        dry_run=False,
        max_tokens=10000,
    )


def _build_editorial_args_for_pipeline(wiki_page_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        page_id=wiki_page_id,
        all=False,
        limit=50,
    )


VALIDATOR_CALLOUT_MARKERS: Tuple[str, ...] = (
    "DeepSeek 校验",
    "Kimi 校验",
    "Gemini 仲裁",
    " · PASS · ",
    " · FAIL · ",
    "维持 FAIL",
    "推翻 FAIL",
    "平均分 ",
    "校验失败",
)


def _collect_validator_callouts(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Top-level callout blocks produced by llm-validate --annotate / Gemini arbiter."""
    result: List[Dict[str, Any]] = []
    for block in blocks:
        if block.get("type") != "callout":
            continue
        text = extract_block_text(block) or ""
        if any(m in text for m in VALIDATOR_CALLOUT_MARKERS):
            result.append(block)
    return result


def _purge_prior_validator_callouts(client: NotionClient, page_id: str) -> int:
    """Delete existing validator/arbiter callouts on a page. Called at pipeline
    entry so each pipeline run leaves only its own callouts behind (instead of
    callouts accumulating across runs)."""
    blocks = iterate_block_children(client, page_id)
    callouts = _collect_validator_callouts(blocks)
    deleted = 0
    for block in callouts:
        bid = block.get("id")
        if not bid:
            continue
        try:
            client.delete_block(bid)
            deleted += 1
        except NotionError as exc:
            print(f"WARN: failed to delete prior callout {bid}: {exc}", file=sys.stderr)
    return deleted


def command_pipeline(
    client: NotionClient,
    env: Dict[str, str],
    raw_database_id: str,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    stages: List[Dict[str, Any]] = []

    # Stage 1: compile-from-raw --auto-refine
    compile_args = _build_compile_args_for_pipeline(args.raw_page_id, force=args.force_refine)
    compile_payload, _ = _capture_command_stdout_json(
        command_compile_from_raw,
        client,
        raw_database_id,
        wiki_database_id,
        mapping,
        compile_args,
    )
    action = compile_payload.get("action")
    wiki_block = compile_payload.get("wiki") or {}
    wiki_page_id = (
        wiki_block.get("page_id")
        or compile_payload.get("page_id")
        or compile_payload.get("target_wiki_page_id")
        or compile_payload.get("existing_wiki_page_id")
    )
    stages.append({"stage": "compile", "status": "ok", "action": action, "wiki_page_id": wiki_page_id})

    if not wiki_page_id:
        stages.append({"stage": "abort", "reason": "no_wiki_page_id_from_compile", "compile_payload_head": {k: compile_payload.get(k) for k in ("action", "error")}})
        print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "overall_status": "error", "stages": stages}), ensure_ascii=False, indent=2))
        return 1

    # Gate A: skipped_unchanged / skipped_duplicate_body → stop unless --force-refine
    if action in ("skipped_unchanged", "skipped_duplicate_body") and not args.force_refine:
        stages.append({"stage": "gate_skip", "reason": f"compile returned {action}; use --force-refine to override"})
        print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "wiki_page_id": wiki_page_id, "overall_status": "skipped_by_gate", "stages": stages}), ensure_ascii=False, indent=2))
        return 0

    # Purge prior-run validator/arbiter callouts so this run's callouts are the
    # only ones left on the page (prevents callout accumulation across repeated
    # pipeline invocations). Individual llm-validate invocations still append
    # without purging — per-page "latest round only" only applies within pipeline.
    if not getattr(args, "keep_prior_callouts", False):
        purged = _purge_prior_validator_callouts(client, wiki_page_id)
        if purged:
            stages.append({"stage": "purge_prior_callouts", "removed": purged})

    # Resolve reader profile (explicit --reader overrides; else auto-infer from wiki page body)
    explicit_reader = getattr(args, "reader", None)
    if explicit_reader and explicit_reader in VALID_READERS:
        resolved_reader = explicit_reader
        reader_source = "explicit"
    else:
        try:
            body_for_infer = read_page_body_text(client, wiki_page_id)
        except NotionError:
            body_for_infer = ""
        resolved_reader = infer_reader_profile(body_for_infer)
        reader_source = "inferred"
    stages.append({"stage": "reader_profile", "reader": resolved_reader, "source": reader_source})

    if args.skip_refine:
        stages.append({"stage": "refine_skipped_by_flag"})
    else:
        # Stage 2: llm-refine-page round 1
        refine_args = _build_refine_page_args_for_pipeline(wiki_page_id, args.refine_provider, reader=resolved_reader)
        try:
            refine_llm = build_llm_client(env, args.refine_provider, None)
            refine_payload, _ = _capture_command_stdout_json(
                command_llm_refine_page,
                client,
                refine_llm,
                wiki_database_id,
                mapping,
                refine_args,
            )
            stages.append({"stage": "refine_round_1", "status": "ok", "section_count": refine_payload.get("section_count"), "model": refine_payload.get("model")})
        except NotionError as exc:
            stages.append({"stage": "refine_round_1", "status": "error", "error": str(exc)})
            print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "wiki_page_id": wiki_page_id, "overall_status": "error", "stages": stages}), ensure_ascii=False, indent=2))
            return 1

    if args.skip_validate:
        stages.append({"stage": "validate_skipped_by_flag"})
        _run_and_append_editorial(client, wiki_database_id, mapping, wiki_page_id, stages, env=env, reader=resolved_reader, autofill=True)
        print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "wiki_page_id": wiki_page_id, "overall_status": "incomplete_no_validate", "stages": stages}), ensure_ascii=False, indent=2))
        return 0

    # Stage 3: llm-validate round 1 (annotate)
    validate_round_1 = _run_validate_capture(client, env, wiki_database_id, mapping, wiki_page_id, args.validate_provider, annotate=True)
    stages.append({"stage": "validate_round_1", **_summarize_validate(validate_round_1)})

    round_1_fails = _collect_fails(validate_round_1)
    if not round_1_fails:
        _run_and_append_editorial(client, wiki_database_id, mapping, wiki_page_id, stages, env=env, reader=resolved_reader, autofill=True)
        print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "wiki_page_id": wiki_page_id, "overall_status": "passed_round_1", "stages": stages}), ensure_ascii=False, indent=2))
        return 0

    # Stage 4: Gemini arbiter — one call per failed heading
    try:
        gemini_client = build_llm_client(env, "gemini", None)
    except NotionError as exc:
        stages.append({"stage": "gemini_init_error", "error": str(exc)})
        print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "wiki_page_id": wiki_page_id, "overall_status": "error_no_gemini", "stages": stages}), ensure_ascii=False, indent=2))
        return 1

    # Read current (post-refine) section bodies for arbiter input
    top_blocks = iterate_block_children(client, wiki_page_id)
    page_meta = client.retrieve_page(wiki_page_id)
    database = client.retrieve_database(wiki_database_id)
    title_prop = mapping.get("title_property") or detect_title_property(database)
    wiki_title = extract_title(page_meta, title_prop) if title_prop else ""

    arbiter_results: List[Dict[str, Any]] = []
    for fail in round_1_fails:
        heading_text = fail.get("heading", "")
        _, body = find_section_body(top_blocks, heading_text)
        current_text = "\n\n".join(extract_block_text(b) for b in body if extract_block_text(b).strip()).strip()
        verdict = gemini_arbitrate(gemini_client, wiki_title, heading_text, current_text, fail)
        arbiter_results.append({
            "heading": heading_text,
            "uphold_fail": verdict.get("uphold_fail"),
            "reasoning": verdict.get("reasoning"),
        })

    upheld = [r for r in arbiter_results if r.get("uphold_fail")]
    overturned = [r for r in arbiter_results if not r.get("uphold_fail")]
    stages.append({
        "stage": "gemini_arbiter",
        "model": gemini_client.model,
        "upheld_count": len(upheld),
        "overturned_count": len(overturned),
        "results": arbiter_results,
    })

    # Add a callout summarizing the arbiter verdict
    _append_arbiter_callout(client, wiki_page_id, gemini_client.model, arbiter_results)

    if not upheld:
        _run_and_append_editorial(client, wiki_database_id, mapping, wiki_page_id, stages, env=env, reader=resolved_reader, autofill=True)
        print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "wiki_page_id": wiki_page_id, "overall_status": "passed_via_gemini_override", "stages": stages}), ensure_ascii=False, indent=2))
        return 0

    # Stage 5: Kimi refine round 2
    refine_args_r2 = _build_refine_page_args_for_pipeline(wiki_page_id, args.refine_provider, reader=resolved_reader)
    # Limit sections to the ones Gemini upheld (save cost)
    refine_args_r2.sections = ",".join(r["heading"] for r in upheld)
    try:
        refine_llm_r2 = build_llm_client(env, args.refine_provider, None)
        _capture_command_stdout_json(
            command_llm_refine_page,
            client,
            refine_llm_r2,
            wiki_database_id,
            mapping,
            refine_args_r2,
        )
        stages.append({"stage": "refine_round_2", "status": "ok", "sections_rewritten": refine_args_r2.sections})
    except NotionError as exc:
        stages.append({"stage": "refine_round_2", "status": "error", "error": str(exc)})
        print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "wiki_page_id": wiki_page_id, "overall_status": "error_round_2_refine", "stages": stages}), ensure_ascii=False, indent=2))
        return 1

    # Stage 6: llm-validate round 2 (annotate)
    validate_round_2 = _run_validate_capture(client, env, wiki_database_id, mapping, wiki_page_id, args.validate_provider, annotate=True)
    stages.append({"stage": "validate_round_2", **_summarize_validate(validate_round_2)})

    round_2_fails = _collect_fails(validate_round_2)
    _run_and_append_editorial(client, wiki_database_id, mapping, wiki_page_id, stages, env=env, reader=resolved_reader, autofill=True)

    overall = "passed_round_2" if not round_2_fails else "round_2_still_failing"
    print(json.dumps(audit_success("pipeline", {"raw_page_id": args.raw_page_id, "wiki_page_id": wiki_page_id, "overall_status": overall, "stages": stages}), ensure_ascii=False, indent=2))
    return 0 if not round_2_fails else 1


def _run_validate_capture(
    client: NotionClient,
    env: Dict[str, str],
    wiki_database_id: str,
    mapping: Dict[str, Any],
    wiki_page_id: str,
    provider: str,
    annotate: bool,
) -> Dict[str, Any]:
    llm = build_llm_client(env, provider, None)
    args = _build_validate_args_for_pipeline(wiki_page_id, provider, annotate)
    payload, _ = _capture_command_stdout_json(
        command_llm_validate,
        client,
        llm,
        wiki_database_id,
        mapping,
        args,
    )
    return payload


def _summarize_validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pass_count": payload.get("pass_count"),
        "fail_count": payload.get("fail_count"),
        "error_count": payload.get("error_count"),
        "avg_score": payload.get("avg_score"),
        "validator_model": payload.get("validator_model"),
    }


def _collect_fails(validate_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = validate_payload.get("results", []) or []
    return [r for r in results if r.get("pass") is False]


def _run_and_append_editorial(
    client: NotionClient,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    wiki_page_id: str,
    stages: List[Dict[str, Any]],
    env: Optional[Dict[str, str]] = None,
    reader: Optional[str] = None,
    autofill: bool = True,
) -> None:
    try:
        result = check_editorial_compliance(client, wiki_database_id, mapping, wiki_page_id)
        stages.append({
            "stage": "check_editorial",
            "compliance": result.get("compliance"),
            "issue_count": result.get("issue_count"),
        })
    except NotionError as exc:
        stages.append({"stage": "check_editorial", "error": str(exc)})
        result = None

    # Phase 3: if editorial yellow with missing_heading issues, auto-fill via
    # judge + Kimi. Only fires when env is available (LLM reachable) and
    # autofill flag is true. Best-effort — failures landed in stages but never
    # abort the pipeline.
    if autofill and env is not None and result is not None:
        missing = [
            i.get("heading")
            for i in (result.get("issues") or [])
            if i.get("check") == "missing_heading" and i.get("heading")
        ]
        if missing:
            try:
                fill_payload = autofill_missing_sections(
                    client,
                    env,
                    wiki_database_id,
                    mapping,
                    wiki_page_id,
                    reader=reader,
                    no_judge=False,
                    provider="kimi",
                )
                stages.append({
                    "stage": "autofill_missing_sections",
                    "compliance_before": fill_payload.get("compliance_before"),
                    "compliance_after": fill_payload.get("compliance_after"),
                    "filled_count": len(fill_payload.get("filled", [])),
                    "skipped_count": len(fill_payload.get("skipped", [])),
                    "filled": [f.get("heading") for f in fill_payload.get("filled", [])],
                })
            except NotionError as exc:
                stages.append({"stage": "autofill_missing_sections", "error": str(exc)})

    # v18 P1 / P2: compute lifecycle + quality states at pipeline end and
    # propagate to Notion (auto-create the Lifecycle / Quality select
    # properties on Wiki DB if missing). Best-effort — failures are logged
    # into stages but never abort the pipeline.
    try:
        ensure_select_property(client, wiki_database_id, "Lifecycle", LIFECYCLE_STATES, LIFECYCLE_COLOR_MAP)
        lifecycle = compute_lifecycle_state(client, wiki_database_id, mapping, wiki_page_id)
        lifecycle["timestamp"] = iso_now()
        _append_states_log({**lifecycle, "kind": "lifecycle", "source": "pipeline"})
        _write_state_to_notion(client, wiki_page_id, "Lifecycle", lifecycle["lifecycle"])
        stages.append({"stage": "lifecycle_state", "state": lifecycle["lifecycle"], "notion_written": True})
    except NotionError as exc:
        stages.append({"stage": "lifecycle_state", "error": str(exc)})
    try:
        ensure_select_property(client, wiki_database_id, "Quality", QUALITY_STATES, QUALITY_COLOR_MAP)
        quality = compute_quality_state(client, wiki_database_id, mapping, wiki_page_id)
        quality["timestamp"] = iso_now()
        _append_states_log({**quality, "kind": "quality", "source": "pipeline"})
        _write_state_to_notion(client, wiki_page_id, "Quality", quality["quality"])
        stages.append({"stage": "quality_state", "state": quality["quality"], "notion_written": True})
    except NotionError as exc:
        stages.append({"stage": "quality_state", "error": str(exc)})


def _append_arbiter_callout(
    client: NotionClient,
    wiki_page_id: str,
    gemini_model: str,
    arbiter_results: List[Dict[str, Any]],
) -> None:
    upheld = [r for r in arbiter_results if r.get("uphold_fail")]
    overturned = [r for r in arbiter_results if not r.get("uphold_fail")]
    intro = (
        f"Gemini 仲裁 · {today_iso_date()} · {gemini_model}\n"
        f"维持 DeepSeek FAIL: {len(upheld)} 段 · 推翻: {len(overturned)} 段"
    )
    blocks: List[Dict[str, Any]] = [{
        "object": "block",
        "type": "callout",
        "callout": {"icon": {"emoji": "⚖️"}, "rich_text": rich_text_value(intro)},
    }]
    for r in arbiter_results:
        h = r.get("heading", "")
        uphold = r.get("uphold_fail")
        reasoning = (r.get("reasoning") or "")[:400]
        verdict = "维持 FAIL" if uphold else "推翻 FAIL"
        emoji = "❌" if uphold else "✅"
        text = f"[{h}] {verdict}\n仲裁理由：{reasoning}"
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {"icon": {"emoji": emoji}, "rich_text": rich_text_value(text)},
        })
    try:
        client.append_block_children(wiki_page_id, blocks)
    except NotionError as exc:
        print(f"WARN: failed to append arbiter callout: {exc}", file=sys.stderr)


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
    compile_parser.add_argument("--append-raw-body-to-wiki", action="store_true", help="Legacy: also append a 增量更新 block to wiki containing raw body (default off as of 2026-04-23; raw body stays on raw page, provenance via Wiki.Source + compile-log)")
    compile_parser.add_argument("--no-judge", action="store_true", help="Disable deepseek-chat alias judge; always fall back to alias-match + review_required (r16 default: judge on)")
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
    queue_parser.add_argument("--append-raw-body-to-wiki", action="store_true", help="Legacy behavior for batch; see compile-from-raw --append-raw-body-to-wiki")
    queue_parser.add_argument("--no-judge", action="store_true", help="Disable deepseek-chat alias judge for batch; see compile-from-raw --no-judge")
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
    cleanup_parser.add_argument("--drop-validator-callouts", action="store_true", help="Also remove top-level callout blocks left behind by llm-validate --annotate and Gemini arbiter")

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

    llm_page_parser = subparsers.add_parser("llm-refine-page")
    llm_page_parser.add_argument("page_id")
    llm_page_parser.add_argument("--sections", default="", help="Comma-separated heading list; default = 定义,为什么重要,关键机制,核心判断,实现信号,与相邻概念的区别")
    llm_page_parser.add_argument("--source-page-id")
    llm_page_parser.add_argument("--style-from-page-id")
    llm_page_parser.add_argument("--style-note", default="")
    llm_page_parser.add_argument("--provider", choices=sorted(LLM_PROVIDERS), default="kimi", help="LLM provider for generation (default: kimi, with deepseek as validator)")
    llm_page_parser.add_argument("--model", default="", help="Model override; default uses provider's default_model")
    llm_page_parser.add_argument("--max-tokens", type=int, default=16000)
    llm_page_parser.add_argument("--temperature", type=float, default=0.4)
    llm_page_parser.add_argument("--mention-map")
    llm_page_parser.add_argument("--link-style", choices=["mention", "link", "both"], default="link")
    llm_page_parser.add_argument("--preview", action="store_true")
    llm_page_parser.add_argument("--reader", choices=sorted(VALID_READERS), help="Reader profile (agent / quant / general); if omitted, auto-inferred from page body keywords")

    validate_parser = subparsers.add_parser("llm-validate")
    validate_parser.add_argument("page_id")
    validate_parser.add_argument("--heading", default="", help="Validate single heading; omit for all registered sections")
    validate_parser.add_argument("--provider", choices=sorted(LLM_PROVIDERS), default="deepseek", help="Validator provider (default: deepseek)")
    validate_parser.add_argument("--model", default="")
    validate_parser.add_argument("--max-tokens", type=int, default=10000)
    validate_parser.add_argument("--annotate", action="store_true", help="Append callout blocks to the wiki page with validation results")
    validate_parser.add_argument("--dry-run", action="store_true", help="With --annotate, compute blocks but do not write to Notion")

    llm_parser = subparsers.add_parser("llm-refine")
    llm_parser.add_argument("page_id", help="Wiki page id to refine")
    llm_parser.add_argument("--heading", required=True, help="Section heading to rewrite")
    llm_parser.add_argument("--source-page-id", help="Optional source wiki/raw page id to include as context")
    llm_parser.add_argument("--extra-instruction", default="", help="Optional extra instruction appended to the prompt")
    llm_parser.add_argument("--style-from-page-id", help="Wiki page id whose 定义 / 核心判断 / 关联概念 sections are fed as few-shot style samples")
    llm_parser.add_argument("--style-note", default="", help="Inline style guidance appended to system prompt (禁令 / 倾向 / 读者层次描述)")
    llm_parser.add_argument("--provider", choices=sorted(LLM_PROVIDERS), default="kimi", help="LLM provider for generation (default: kimi, with deepseek as validator)")
    llm_parser.add_argument("--model", default="", help="Model override; default uses provider's default_model")
    llm_parser.add_argument("--max-tokens", type=int, default=10000)
    llm_parser.add_argument("--temperature", type=float, default=0.4)
    llm_parser.add_argument("--mention-map", help="Apply LABEL=page_id mentions to the generated body")
    llm_parser.add_argument("--link-style", choices=["mention", "link", "both"], default="link")
    llm_parser.add_argument("--preview", action="store_true", help="Show what would be written without touching Notion")
    llm_parser.add_argument("--reader", choices=sorted(VALID_READERS), help="Reader profile (agent / quant / general); if omitted, auto-inferred from page body keywords")

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

    review_parser = subparsers.add_parser(
        "list-review-queue",
        help="Aggregate review signals (editorial yellow/red, alias/fuzzy review_required, Verification=Needs Review, compile failures) into decision records.",
    )
    review_parser.add_argument(
        "--source",
        default="all",
        help="Comma-separated subset of {editorial,audit,verification,failures} or 'all'",
    )
    review_parser.add_argument("--editorial-limit", type=int, default=50, help="Max wiki pages to scan for editorial check")
    review_parser.add_argument("--days", type=int, default=7, help="Lookback window in days for audit-log / compile failures")
    review_parser.add_argument("--emit-decisions", action="store_true", help="Append new (unseen) signals to decisions.jsonl as decision records")
    review_parser.add_argument("--dry-run", action="store_true", help="With --emit-decisions, preview what would be appended without writing")

    resolve_parser = subparsers.add_parser(
        "resolve-decision",
        help="Transition a decision id to a terminal status by appending a resolution record to decisions.jsonl.",
    )
    resolve_parser.add_argument("id", help="Decision id (from list-review-queue output)")
    resolve_parser.add_argument("--status", required=True, choices=sorted(DECISION_STATUSES - {"open"}), help="Target status")
    resolve_parser.add_argument("--rationale", default="", help="Free-text explanation of the decision")
    resolve_parser.add_argument("--resolver", default="", help="Who made the decision; defaults to 'session-layer'")

    lifecycle_parser = subparsers.add_parser(
        "compute-lifecycle-state",
        help="v18 P1: compute lifecycle state (growing / stable / stale / conflicted) for a wiki page from observable signals (Compounded Level / Last Compounded At / editorial / recent diff conflicts).",
    )
    lifecycle_parser.add_argument("page_id", nargs="?", default="", help="Wiki page id; omit when using --all")
    lifecycle_parser.add_argument("--all", action="store_true", help="Compute for every page in the Wiki database")
    lifecycle_parser.add_argument("--limit", type=int, default=50)
    lifecycle_parser.add_argument("--write-notion", action="store_true", help="Write the computed state to Wiki.<notion-property> (select). Auto-creates the property with {growing,stable,stale,conflicted} options if missing.")
    lifecycle_parser.add_argument("--notion-property", default="Lifecycle", help="Target select property name (default: Lifecycle)")

    quality_parser = subparsers.add_parser(
        "compute-quality-state",
        help="v18 P2: aggregate check-editorial + latest llm-validate + open decisions into a single quality state (draft / review_required / validated / ready) for a wiki page.",
    )
    quality_parser.add_argument("page_id", nargs="?", default="", help="Wiki page id; omit when using --all")
    quality_parser.add_argument("--all", action="store_true", help="Compute for every page in the Wiki database")
    quality_parser.add_argument("--limit", type=int, default=50)
    quality_parser.add_argument("--write-notion", action="store_true", help="Write the computed state to Wiki.<notion-property> (select). Auto-creates the property with {draft,review_required,validated,ready} options if missing.")
    quality_parser.add_argument("--notion-property", default="Quality", help="Target select property name (default: Quality)")

    autofill_parser = subparsers.add_parser(
        "autofill-missing-sections",
        help="Phase 3 · For a wiki page with editorial=yellow (missing required heading), decide via deepseek-chat judge whether the page has enough content to fill; if fill, append an empty heading_2 and delegate content generation to llm-refine-page (Kimi).",
    )
    autofill_parser.add_argument("page_id", help="Wiki page id")
    autofill_parser.add_argument("--provider", choices=sorted(LLM_PROVIDERS), default="kimi", help="Generator provider for the new section body (default: kimi)")
    autofill_parser.add_argument("--no-judge", action="store_true", help="Skip fill/skip judge; fill every missing required heading in priority order")
    autofill_parser.add_argument("--reader", choices=sorted(VALID_READERS), help="Reader profile for prompt composition; auto-inferred if omitted")

    pipeline_parser = subparsers.add_parser(
        "pipeline",
        help="Full T1+T2 flow for one raw: compile-from-raw --auto-refine → llm-refine-page (Kimi) → llm-validate (DeepSeek, annotate) → Gemini arbiter on FAIL → Kimi round 2 if upheld → check-editorial. Max 2 Kimi rounds.",
    )
    pipeline_parser.add_argument("raw_page_id", help="Raw Inbox page id to compile")
    pipeline_parser.add_argument("--refine-provider", choices=sorted(LLM_PROVIDERS), default="kimi", help="Primary generator (default: kimi)")
    pipeline_parser.add_argument("--validate-provider", choices=sorted(LLM_PROVIDERS), default="deepseek", help="Validator (default: deepseek)")
    pipeline_parser.add_argument("--force-refine", action="store_true", help="Even if compile returns skipped_unchanged/skipped_duplicate_body, continue into LLM refine (default: stop)")
    pipeline_parser.add_argument("--skip-refine", action="store_true", help="Skip llm-refine-page stage; go straight from compile to validate")
    pipeline_parser.add_argument("--skip-validate", action="store_true", help="Skip llm-validate + Gemini arbiter (only compile + optional refine)")
    pipeline_parser.add_argument("--reader", choices=sorted(VALID_READERS), help="Reader profile (agent / quant / general); if omitted, auto-inferred from wiki page body keywords")
    pipeline_parser.add_argument("--keep-prior-callouts", action="store_true", help="Keep callouts from earlier pipeline runs; by default prior validator/arbiter callouts are purged before this run's annotations so only the latest round remains")

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
            env=env,
        )
    if args.command == "compile-queue":
        args._env = env
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
    if args.command == "llm-refine":
        llm = build_llm_client(env, args.provider, args.model or None)
        return command_llm_refine(
            client,
            llm,
            require_env(env, "NOTION_WIKI_DB_ID"),
            mapping,
            args,
        )
    if args.command == "llm-refine-page":
        llm = build_llm_client(env, args.provider, args.model or None)
        return command_llm_refine_page(
            client,
            llm,
            require_env(env, "NOTION_WIKI_DB_ID"),
            mapping,
            args,
        )
    if args.command == "llm-validate":
        llm = build_llm_client(env, args.provider, args.model or None)
        return command_llm_validate(
            client,
            llm,
            require_env(env, "NOTION_WIKI_DB_ID"),
            mapping,
            args,
        )
    if args.command == "reference-check":
        return command_reference_check(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "seed-related-pages":
        return command_seed_related_pages(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "lint":
        return command_lint(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "list-review-queue":
        return command_list_review_queue(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "resolve-decision":
        return command_resolve_decision(args)
    if args.command == "compute-lifecycle-state":
        return command_compute_lifecycle_state(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "compute-quality-state":
        return command_compute_quality_state(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "autofill-missing-sections":
        return command_autofill_missing_sections(client, env, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "pipeline":
        return command_pipeline(
            client,
            env,
            require_env(env, "NOTION_RAW_INBOX_DB_ID"),
            require_env(env, "NOTION_WIKI_DB_ID"),
            mapping,
            args,
        )
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
