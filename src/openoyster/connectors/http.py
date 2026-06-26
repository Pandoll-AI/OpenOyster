from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..utils import normalise_text, sha256_text, stable_hash

HTTP_PARSER_VERSION = "http-v2"
_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
)


@dataclass(frozen=True)
class HttpDocument:
    source: str
    source_uri: str
    title: str
    text: str
    content_hash: str
    ingest_key: str
    parser_version: str
    metadata: dict[str, Any]


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// URLs are supported")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are rejected")
    try:
        addresses = {
            str(item[4][0])
            for item in socket.getaddrinfo(
                parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
            )
        }
    except socket.gaierror as exc:
        raise ValueError(f"Unable to resolve hostname: {parsed.hostname}") from exc
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise ValueError("URL resolves to a non-public address and was blocked")


def _decode_response(response: httpx.Response) -> tuple[str, str]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if not any(content_type.startswith(allowed) for allowed in _ALLOWED_CONTENT_TYPES):
        raise ValueError(f"Unsupported HTTP content type: {content_type or '<missing>'}")
    text = response.text
    title = str(response.url)
    if "html" in content_type:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        text = soup.get_text("\n", strip=True)
    elif content_type == "application/json":
        text = json.dumps(response.json(), ensure_ascii=False, indent=2)
    return normalise_text(text), title


def fetch_url(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 20.0,
    max_redirects: int = 3,
    source: str = "http",
) -> HttpDocument:
    current = url
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
        for redirect_count in range(max_redirects + 1):
            validate_public_http_url(current)
            with client.stream("GET", current, headers={"User-Agent": "OpenOyster/0.2"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count >= max_redirects:
                        raise ValueError("HTTP redirect limit exceeded")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ValueError(f"HTTP response exceeds {max_bytes} bytes")
                materialised = httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    content=bytes(body),
                    request=response.request,
                )
                text, title = _decode_response(materialised)
                if not text:
                    raise ValueError("HTTP resource contains no readable text")
                content_hash = sha256_text(text)
                return HttpDocument(
                    source=source,
                    source_uri=str(materialised.url),
                    title=title,
                    text=text,
                    content_hash=content_hash,
                    ingest_key=stable_hash(str(materialised.url), content_hash, HTTP_PARSER_VERSION),
                    parser_version=HTTP_PARSER_VERSION,
                    metadata={
                        "content_type": response.headers.get("content-type"),
                        "size_bytes": len(body),
                        "redirects": redirect_count,
                    },
                )
    raise RuntimeError("Unreachable HTTP connector state")
