from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import inspect

from openoyster.config import Settings
from openoyster.connectors.filesystem import parse_file
from openoyster.connectors.github import fetch_github_items
from openoyster.connectors.http import validate_public_http_url
from openoyster.connectors.rss import parse_rss
from openoyster.database import make_engine, upgrade_database
from openoyster.policies import DEFAULT_POLICY, get_nested, set_nested, validate_policy
from openoyster.scoring import weighted_trigger_score
from openoyster.services.evaluation import evaluate_fixture_path


def test_nested_policy_helpers_do_not_mutate_original():
    updated = set_nested(DEFAULT_POLICY, "trigger.fire_threshold", 0.77)
    assert get_nested(updated, "trigger.fire_threshold") == 0.77
    assert get_nested(DEFAULT_POLICY, "trigger.fire_threshold") != 0.77
    validate_policy(updated)


def test_weighted_trigger_score_is_bounded():
    score = weighted_trigger_score(
        novelty=10,
        impact=10,
        contradiction=10,
        evidence_gap=10,
        staleness=10,
        policy=DEFAULT_POLICY,
    )
    assert 0 <= score <= 1


def test_filesystem_parsers_and_size_guard(tmp_path: Path):
    json_file = tmp_path / "data.json"
    json_file.write_text(json.dumps({"Acme": {"risk": "governance"}}), encoding="utf-8")
    parsed = parse_file(json_file, max_bytes=10_000)
    assert "governance" in parsed.text
    with pytest.raises(ValueError, match="max size"):
        parse_file(json_file, max_bytes=1)


def test_ssrf_guard_rejects_loopback():
    with pytest.raises(ValueError, match="non-public"):
        validate_public_http_url("http://127.0.0.1/private")


def test_alembic_initial_migration(tmp_path: Path):
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'migrated.db'}",
        workspace=tmp_path / "workspace",
    )
    upgrade_database(settings)
    engine = make_engine(settings)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"events", "hypotheses", "policies", "alembic_version"} <= tables


def test_rss_connector_parses_items_without_network(monkeypatch):
    xml = """<?xml version="1.0"?>
    <rss><channel><item><title>Governance launch</title><link>https://example.com/a</link>
    <guid>a-1</guid><pubDate>2026-01-01</pubDate>
    <description><![CDATA[Acme launched a governance product.]]></description></item></channel></rss>"""
    monkeypatch.setattr("openoyster.connectors.rss.validate_public_http_url", lambda url: None)
    monkeypatch.setattr("openoyster.connectors.rss._fetch_feed", lambda *args, **kwargs: xml)
    documents = parse_rss("https://example.com/feed.xml", max_bytes=10_000, timeout_seconds=1)
    assert len(documents) == 1
    assert documents[0].source == "rss"
    assert "governance product" in documents[0].text
    assert documents[0].metadata["item_id"] == "a-1"


def test_github_connector_does_not_persist_token(monkeypatch):
    monkeypatch.setattr(
        "openoyster.connectors.github._get_json",
        lambda *args, **kwargs: [
            {
                "id": 123,
                "tag_name": "v1.2.3",
                "name": "Release v1.2.3",
                "html_url": "https://github.com/acme/demo/releases/tag/v1.2.3",
                "published_at": "2026-01-02T00:00:00Z",
                "body": "Governance release with audit logs.",
            }
        ],
    )
    documents = fetch_github_items(
        "acme/demo",
        kind="releases",
        token="secret-token",
        max_bytes=10_000,
        timeout_seconds=1,
    )
    assert len(documents) == 1
    assert documents[0].source == "github:acme/demo"
    assert "secret-token" not in json.dumps(documents[0].metadata)


def test_fixture_evaluation_reports_quality_metrics(tmp_path: Path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "fixture",
                "documents": [
                    {
                        "text": (
                            "Acme launched a product but no customer confirmed adoption. "
                            "The strategic risk is governance approval delay."
                        )
                    }
                ],
                "expected_signal_types": ["product_release", "risk", "governance"],
                "expected_counter_terms": ["no customer"],
            }
        ),
        encoding="utf-8",
    )
    report = evaluate_fixture_path(fixture)
    assert report["fixture_count"] == 1
    assert set(report["aggregate"]) == {
        "signal_type_precision",
        "signal_type_recall",
        "counter_evidence_discovery_rate",
        "artifact_traceability",
    }
