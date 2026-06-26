# OpenOyster Connectors

Connectors turn external source items into durable documents. They are perception adapters, not reasoning loops.

## 1. Filesystem connector

The intake loop scans `OPENOYSTER_INBOX_DIR` and supports:

| Type | Extensions | Parser behaviour |
|---|---|---|
| Plain text | `.txt`, `.md`, `.markdown`, `.log` | UTF-8 text with normalisation. |
| Structured text | `.json`, `.jsonl`, `.yaml`, `.yml` | Parsed and rendered into readable text. |
| Tabular | `.csv`, `.tsv` | Rows rendered with bounded textual structure. |
| HTML | `.html`, `.htm` | Scripts/styles removed; visible text extracted. |
| PDF | `.pdf` | Text extraction using pypdf; image-only scans need a separate OCR connector. |
| Word | `.docx` | Paragraph and table text extraction. |

Files larger than `OPENOYSTER_MAX_FILE_BYTES` are rejected. A source fingerprint detects file changes; content hash and parser version produce a content-versioned ingest key.

The intake loop records failed source items individually. It does not roll back previously successful files in the same scan.

## 2. HTTP connector

Use:

```bash
openoyster ingest-url https://example.org/report
```

or `POST /v1/ingest-url`.

Controls:

- only `http` and `https`;
- no URL credentials;
- DNS resolution before each request/redirect;
- block private, loopback, link-local, reserved, multicast, and unspecified IPs;
- redirect limit;
- timeout;
- streaming response-size limit;
- allow-list of text/JSON/XML/HTML content types;
- visible-text HTML extraction.

Residual SSRF risk still exists in hostile DNS/network environments. Use egress firewall rules and an HTTP proxy allow-list for high-assurance deployment.

The HTTP connector fetches one resource. It does not implement crawling, robots policy, scheduling, rate limiting per host, or authentication.

## 3. RSS connector

Use:

```bash
openoyster ingest-rss feeds.yaml
```

`feeds.yaml` can be either:

```yaml
feeds:
  - https://example.org/feed.xml
```

or a plain YAML list of feed URLs. The connector parses RSS and Atom items into documents with feed URL, item ID, published timestamp, source URI, and parser version metadata.

Controls:

- only public `http` and `https` feed URLs;
- redirect, timeout, and response-size limits;
- HTML stripping for item summaries/content;
- stable ingest keys using feed URL, item ID, content hash, and parser version.

It does not crawl linked articles, authenticate feeds, or implement per-host scheduling.

## 4. GitHub connector

Use:

```bash
openoyster ingest-github owner/repo --kind releases
openoyster ingest-github owner/repo --kind issues
```

The connector reads public GitHub REST API responses for releases or issues. It excludes pull requests from issue ingestion. `OPENOYSTER_GITHUB_TOKEN` may be set for higher API limits, but the token is not persisted in document metadata or events.

Controls:

- repository names must be `owner/name`;
- read-only release/issue endpoints only;
- timeout and response-size limits;
- stable ingest keys using repo, kind, item ID, content hash, and parser version;
- issue comments are intentionally excluded in `0.3.0`.

## 5. Connector data contract

A parsed read connector should return:

```python
@dataclass(frozen=True)
class ParsedDocument:
    source: str
    source_uri: str
    title: str
    text: str
    content_hash: str
    ingest_key: str
    parser_version: str
    metadata: dict[str, Any]
```

`ingest_key` should include stable source identity, content hash, and parser version. A parser upgrade can intentionally create a new document version.

## 6. Adding a connector

1. Define source-item discovery and a stable fingerprint.
2. Bound file/network size, time, and retries.
3. Parse into normalised text and provenance metadata.
4. Persist `SourceItem` state.
5. Persist `Document` only after parsing succeeds.
6. Emit `doc.fetched` with an idempotency key.
7. Test unchanged, changed, malformed, malicious, and oversized inputs.
8. Document credentials, rate limits, privacy, and retention.

## 7. Authenticated sources

Do not place access tokens in source URLs or event payloads. Use a secret store or environment injection, redact headers, and persist only a non-secret source identity. GitHub token support follows this rule by reading `OPENOYSTER_GITHUB_TOKEN` and excluding it from metadata.

## 8. Write connectors

Write connectors are actions, not intake. They must use a separate approval-gated tool contract. A PR that adds email sending, deployment, issue creation, trading, record mutation, or deletion without approval and audit boundaries will not be accepted.

## 9. OCR and media

The default PDF parser extracts embedded text only. OCR, images, audio, and video require dedicated connectors with explicit compute limits, language/model provenance, and confidence metadata.
