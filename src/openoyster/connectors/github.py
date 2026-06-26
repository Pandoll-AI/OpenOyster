from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from ..utils import normalise_text, sha256_text, stable_hash

GITHUB_PARSER_VERSION = "github-v1"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class GitHubDocument:
    source: str
    source_uri: str
    title: str
    text: str
    content_hash: str
    ingest_key: str
    parser_version: str = GITHUB_PARSER_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OpenOyster/0.3",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(
    url: str,
    *,
    token: str | None,
    timeout_seconds: float,
    max_bytes: int,
) -> list[dict[str, Any]]:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
        response = client.get(url, headers=_headers(token))
        response.raise_for_status()
        if len(response.content) > max_bytes:
            raise ValueError(f"GitHub response exceeds {max_bytes} bytes")
        payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("GitHub API response must be a list")
    return [item for item in payload if isinstance(item, dict)]


def _release_document(repo: str, item: dict[str, Any]) -> GitHubDocument:
    title = str(item.get("name") or item.get("tag_name") or "GitHub release")
    html_url = str(item.get("html_url") or f"https://github.com/{repo}/releases")
    body = normalise_text(str(item.get("body") or ""))
    published = str(item.get("published_at") or item.get("created_at") or "")
    tag = str(item.get("tag_name") or "")
    text = normalise_text(
        "\n".join(
            part
            for part in (
                f"Repository: {repo}",
                f"Release: {title}",
                f"Tag: {tag}" if tag else "",
                f"Published: {published}" if published else "",
                body,
            )
            if part
        )
    )
    content_hash = sha256_text(text)
    item_id = str(item.get("id") or tag or html_url)
    return GitHubDocument(
        source=f"github:{repo}",
        source_uri=html_url,
        title=title,
        text=text,
        content_hash=content_hash,
        ingest_key=stable_hash("github", repo, "releases", item_id, content_hash, GITHUB_PARSER_VERSION),
        metadata={
            "repo": repo,
            "kind": "releases",
            "item_id": item_id,
            "tag": tag,
            "published": published,
            "parser": GITHUB_PARSER_VERSION,
        },
    )


def _issue_document(repo: str, item: dict[str, Any]) -> GitHubDocument | None:
    if "pull_request" in item:
        return None
    number = item.get("number")
    title = str(item.get("title") or f"Issue {number}")
    html_url = str(item.get("html_url") or f"https://github.com/{repo}/issues/{number}")
    body = normalise_text(str(item.get("body") or ""))
    state = str(item.get("state") or "")
    updated = str(item.get("updated_at") or item.get("created_at") or "")
    labels = [
        str(label.get("name"))
        for label in item.get("labels", [])
        if isinstance(label, dict) and label.get("name")
    ]
    text = normalise_text(
        "\n".join(
            part
            for part in (
                f"Repository: {repo}",
                f"Issue #{number}: {title}",
                f"State: {state}" if state else "",
                f"Updated: {updated}" if updated else "",
                f"Labels: {', '.join(labels)}" if labels else "",
                body,
            )
            if part
        )
    )
    content_hash = sha256_text(text)
    item_id = str(item.get("id") or number or html_url)
    return GitHubDocument(
        source=f"github:{repo}",
        source_uri=html_url,
        title=title,
        text=text,
        content_hash=content_hash,
        ingest_key=stable_hash("github", repo, "issues", item_id, content_hash, GITHUB_PARSER_VERSION),
        metadata={
            "repo": repo,
            "kind": "issues",
            "item_id": item_id,
            "number": number,
            "state": state,
            "labels": labels,
            "updated": updated,
            "parser": GITHUB_PARSER_VERSION,
        },
    )


def fetch_github_items(
    repo: str,
    *,
    kind: Literal["releases", "issues"],
    token: str | None,
    max_bytes: int,
    timeout_seconds: float,
    limit: int = 25,
) -> list[GitHubDocument]:
    if not _REPO.match(repo):
        raise ValueError("GitHub repo must be in owner/name form")
    if kind == "releases":
        url = f"https://api.github.com/repos/{repo}/releases?per_page={limit}"
    else:
        url = f"https://api.github.com/repos/{repo}/issues?state=all&per_page={limit}"
    payload = _get_json(url, token=token, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
    documents: list[GitHubDocument] = []
    for item in payload[:limit]:
        document = _release_document(repo, item) if kind == "releases" else _issue_document(repo, item)
        if document is not None and document.text:
            documents.append(document)
    return documents
