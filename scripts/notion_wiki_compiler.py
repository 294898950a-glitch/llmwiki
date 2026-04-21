#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
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

    def append_block_children(self, block_id: str, children: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self.request("PATCH", f"blocks/{block_id}/children", {"children": children})


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
    if prop_type == "select":
        value = prop.get("select")
        return value.get("name", "") if value else ""
    if prop_type == "status":
        value = prop.get("status")
        return value.get("name", "") if value else ""
    if prop_type == "unique_id":
        value = prop.get("unique_id")
        if not value:
            return ""
        prefix = value.get("prefix") or ""
        number = value.get("number")
        return f"{prefix}{number}" if number is not None else ""
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


def read_page_body_text(client: NotionClient, page_id: str) -> str:
    response = client.retrieve_block_children(page_id)
    lines: List[str] = []
    for block in response.get("results", []):
        text = extract_block_text(block)
        if text:
            lines.append(text)
    return "\n\n".join(lines).strip()


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

    pages = query_database_pages(client, database_id, filter_body)
    normalized_canonical_id = normalize(canonical_id)
    matches = [
        page
        for page in pages
        if normalize(extract_property_text(page, canonical_property)) == normalized_canonical_id
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
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text_value(note)},
        },
    ]
    if source_url:
        blocks.append(
            {
                "object": "block",
                "type": "bookmark",
                "bookmark": {"url": source_url},
            }
        )
    return blocks


def resolve_title_property_name(database: Dict[str, Any], mapping: Dict[str, Any], cli_value: Optional[str]) -> str:
    title_prop = cli_value or mapping.get("title_property") or detect_title_property(database)
    if not title_prop:
        raise NotionError("Unable to determine title property")
    return title_prop


def upsert_note_to_wiki(
    client: NotionClient,
    database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    database = client.retrieve_database(database_id)
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

    exact_match = None
    for page in candidates:
        if args.canonical_id and canonical_prop:
            if normalize(extract_property_text(page, canonical_prop)) == normalize(args.canonical_id):
                exact_match = page
                break
        if page_matches_query(page, args.title, title_prop, aliases_prop):
            exact_match = page
            break

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
        return {"action": "updated", "page_id": exact_match["id"], "title": args.title}

    payload: Dict[str, Any] = {
        "parent": {"database_id": database_id},
        "properties": build_properties(database, mapping, args),
        "children": build_append_blocks(args.note, append_heading, args.source_url),
    }
    created = client.create_page(payload)
    return {"action": "created", "page_id": created.get("id"), "title": args.title}


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
    print(json.dumps(payload, ensure_ascii=False, indent=2))
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
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def command_upsert(client: NotionClient, database_id: str, mapping: Dict[str, Any], args: argparse.Namespace) -> int:
    result = upsert_note_to_wiki(client, database_id, mapping, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_compile_from_raw(
    client: NotionClient,
    raw_database_id: str,
    wiki_database_id: str,
    mapping: Dict[str, Any],
    args: argparse.Namespace,
) -> int:
    raw_page = client.retrieve_page(args.page_id)
    if normalize_notion_id(database_parent_id(raw_page)) != normalize_notion_id(raw_database_id):
        raise NotionError("Raw page does not belong to NOTION_RAW_INBOX_DB_ID")

    raw_database = client.retrieve_database(raw_database_id)
    raw_title_property = args.raw_title_property or mapping.get("raw_title_property") or detect_title_property(raw_database)
    if not raw_title_property:
        raise NotionError("Unable to determine raw title property")

    title = args.title or extract_title(raw_page, raw_title_property)
    if not title:
        raise NotionError("Raw page title is empty; pass --title explicitly")

    note = read_page_body_text(client, args.page_id)
    if not note:
        raise NotionError("Raw page body is empty; nothing to compile")

    source_prop_name = args.raw_source_url_property or mapping.get("raw_source_url_property")
    source_url = extract_property_text(raw_page, source_prop_name) if source_prop_name else None

    upsert_args = argparse.Namespace(
        title=title,
        note=note,
        source_url=source_url,
        canonical_id=args.canonical_id,
        verification=args.verification,
        compounded_level=args.compounded_level,
        last_compounded_at=args.last_compounded_at,
        append_heading=args.append_heading,
        increment_compounded_level=args.increment_compounded_level,
        title_property=args.title_property,
        canonical_id_property=args.canonical_id_property,
        verification_property=args.verification_property,
        compounded_level_property=args.compounded_level_property,
        last_compounded_at_property=args.last_compounded_at_property,
    )
    wiki_result = upsert_note_to_wiki(client, wiki_database_id, mapping, upsert_args)

    raw_props_meta = raw_database.get("properties", {})
    raw_updates: Dict[str, Any] = {}
    status_prop_name = args.raw_status_property or mapping.get("raw_status_property")
    processed_at_prop_name = args.raw_processed_at_property or mapping.get("raw_processed_at_property")
    target_prop_name = args.raw_target_wiki_page_property or mapping.get("raw_target_wiki_page_property")
    compiled_status = args.raw_compiled_status or mapping.get("raw_compiled_status", "Compiled")

    if status_prop_name and status_prop_name in raw_props_meta:
        raw_updates[status_prop_name] = property_payload_for_value(raw_props_meta[status_prop_name], compiled_status)
    if processed_at_prop_name and processed_at_prop_name in raw_props_meta:
        raw_updates[processed_at_prop_name] = property_payload_for_value(raw_props_meta[processed_at_prop_name], iso_now())
    if target_prop_name and target_prop_name in raw_props_meta:
        raw_updates[target_prop_name] = property_payload_for_value(raw_props_meta[target_prop_name], [wiki_result["page_id"]])
    if raw_updates:
        client.update_page(args.page_id, {"properties": raw_updates})

    payload = {
        "timestamp": iso_now(),
        "action": "compiled",
        "raw_page_id": args.page_id,
        "raw_title": title,
        "wiki": wiki_result,
        "raw_updates": list(raw_updates.keys()),
        "source_url": source_url,
    }
    log_path = append_jsonl_log(
        f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')}-compile-log.jsonl",
        payload,
    )
    payload["log_path"] = str(log_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


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
            "filter": {
                "property": verification_prop,
                props_meta[verification_prop]["type"]: {"equals": value},
            }
        }
        results = client.query_database(database_id, filter_body).get("results", [])
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
    print(json.dumps(hits, ensure_ascii=False, indent=2))
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
    if args.command == "upsert-note":
        return command_upsert(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    if args.command == "lint":
        return command_lint(client, require_env(env, "NOTION_WIKI_DB_ID"), mapping, args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NotionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
