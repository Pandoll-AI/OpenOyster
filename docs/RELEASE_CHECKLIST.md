# OpenOyster Release Checklist

## Code and tests

- [ ] Version updated in `pyproject.toml`, `openoyster.__version__`, API metadata, and changelog.
- [ ] `ruff check src tests` passes.
- [ ] `mypy src/openoyster` passes.
- [ ] `pytest --cov-fail-under=75` passes on Python 3.11–3.13.
- [ ] Alembic migration succeeds on a fresh database.
- [ ] Upgrade test succeeds from the previous release schema.
- [ ] CLI lifecycle succeeds in an isolated workspace.
- [ ] API authentication, escaping, and readiness tests pass.
- [ ] Wheel and sdist build successfully.
- [ ] Wheel contains Alembic environment, templates, and versions.
- [ ] Installed-wheel `openoyster --help` and import smoke tests pass.

## Security and operations

- [ ] Dependency and container vulnerability scans reviewed.
- [ ] No secrets, runtime databases, private documents, or logs in the archive.
- [ ] New connectors have size/time/SSRF/path protections.
- [ ] New write tools have approval and idempotency boundaries.
- [ ] Backup and restore procedure tested for affected data changes.
- [ ] Docker/Compose configuration reviewed; default writes are not open.
- [ ] Threat model updated.

## Documentation

- [ ] README status is honest and does not claim production readiness without evidence.
- [ ] User manual reflects all commands and configuration names.
- [ ] Contributor manual reflects event/model/policy changes.
- [ ] API and connector docs updated.
- [ ] Changelog includes breaking changes and migrations.
- [ ] Goal roadmap and known limitations updated.
- [ ] Release notes include environments actually exercised.

## Packaging

- [ ] Caches, coverage files, workspaces, databases, and build leftovers removed.
- [ ] Archive root is `OpenOyster/`.
- [ ] Archive extracted and installed in a clean temporary environment.
- [ ] SHA-256 checksum generated and recorded.
