from __future__ import annotations

from openoyster.cli import _redact_url


def test_redact_url_masks_userinfo_and_secret_query_values() -> None:
    redacted = _redact_url(
        "postgresql://user:supersecret@example.com/db?ssl_password=sslsecret&application_name=openoyster"
    )

    assert "supersecret" not in redacted
    assert "sslsecret" not in redacted
    assert "ssl_password=%2A%2A%2A" in redacted
    assert "application_name=openoyster" in redacted
