from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml
from bs4 import BeautifulSoup

from ..utils import normalise_text, sha256_text, stable_hash
from .http import validate_public_http_url

RSS_PARSER_VERSION = "rss-v1"


@dataclass(frozen=True)
class FeedDocument:
    source: str
    source_uri: str
    title: str
    text: str
    content_hash: str
    ingest_key: str
    parser_version: str = RSS_PARSER_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_feed_config(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(payload, list):
        feeds = payload
    elif isinstance(payload, dict):
        feeds = payload.get("feeds", [])
    else:
        raise ValueError("RSS config must be a list or a mapping with a feeds list")
    urls: list[str] = []
    for item in feeds:
        url = item.get("url") if isinstance(item, dict) else item
        if not isinstance(url, str) or not url.strip():
            raise ValueError("Every RSS feed entry must contain a URL string")
        validate_public_http_url(url)
        urls.append(url)
    return urls


def _fetch_feed(url: str, *, max_bytes: int, timeout_seconds: float, max_redirects: int = 3) -> str:
    current = url
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
        for redirect_count in range(max_redirects + 1):
            validate_public_http_url(current)
            with client.stream("GET", current, headers={"User-Agent": "OpenOyster/0.3"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count >= max_redirects:
                        raise ValueError("RSS redirect limit exceeded")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ValueError(f"RSS response exceeds {max_bytes} bytes")
                return bytes(body).decode(response.encoding or "utf-8", errors="replace")
    raise RuntimeError("Unreachable RSS connector state")


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else normalise_text(element.text)


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalise_text(soup.get_text("\n", strip=True))


def _children(parent: ET.Element, names: set[str]) -> list[ET.Element]:
    return [child for child in parent.iter() if child.tag.split("}")[-1] in names]


def _first(parent: ET.Element, names: set[str]) -> ET.Element | None:
    for child in _children(parent, names):
        return child
    return None


def _entry_documents(root: ET.Element, feed_url: str, item_limit: int) -> list[FeedDocument]:
    candidates = _children(root, {"item"}) or _children(root, {"entry"})
    documents: list[FeedDocument] = []
    for index, item in enumerate(candidates[:item_limit]):
        title = _text(_first(item, {"title"})) or f"Feed item {index + 1}"
        link = _text(_first(item, {"link"}))
        link_element = _first(item, {"link"})
        if not link and link_element is not None:
            link = link_element.attrib.get("href", "")
        item_id = _text(_first(item, {"guid", "id"})) or link or stable_hash(feed_url, index, title)
        published = _text(_first(item, {"published", "updated", "pubDate"}))
        summary = _text(_first(item, {"summary", "description", "content"}))
        body = _html_to_text(summary)
        text = normalise_text(
            "\n".join(
                part
                for part in (
                    f"Title: {title}",
                    f"Published: {published}" if published else "",
                    f"URL: {link}" if link else "",
                    body,
                )
                if part
            )
        )
        if not text:
            continue
        content_hash = sha256_text(text)
        source_uri = link or f"{feed_url}#{item_id}"
        documents.append(
            FeedDocument(
                source="rss",
                source_uri=source_uri,
                title=title,
                text=text,
                content_hash=content_hash,
                ingest_key=stable_hash("rss", feed_url, item_id, content_hash, RSS_PARSER_VERSION),
                metadata={
                    "feed_url": feed_url,
                    "item_id": item_id,
                    "published": published,
                    "parser": RSS_PARSER_VERSION,
                },
            )
        )
    return documents


def parse_rss(
    feed_url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    item_limit: int = 20,
) -> list[FeedDocument]:
    validate_public_http_url(feed_url)
    raw = _fetch_feed(feed_url, max_bytes=max_bytes, timeout_seconds=timeout_seconds)
    root = ET.fromstring(raw)
    return _entry_documents(root, feed_url, item_limit)
