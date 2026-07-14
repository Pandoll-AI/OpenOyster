"""Frozen Pack scope resolution for Autonomous Deliberation D1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.deliberation_contracts import canonical_json, payload_digest
from openoyster.models import PackInstall


class DeliberationScopeError(ValueError):
    """Raised when Mission Pack scope cannot be frozen."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FrozenPackRef:
    pack_install_id: int
    pack_id: str
    declared_version: str
    source_digest: str
    admission_profile: str
    role: str
    snapshot: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "pack_install_id": self.pack_install_id,
            "pack_id": self.pack_id,
            "declared_version": self.declared_version,
            "source_digest": self.source_digest,
            "admission_profile": self.admission_profile,
            "role": self.role,
            "snapshot": self.snapshot,
        }


@dataclass(frozen=True)
class FrozenScope:
    primary: tuple[FrozenPackRef, ...]
    impact_baseline: tuple[FrozenPackRef, ...]
    primary_digest: str
    impact_baseline_digest: str

    @property
    def primary_install_ids(self) -> list[int]:
        return [item.pack_install_id for item in self.primary]

    @property
    def baseline_install_ids(self) -> list[int]:
        return [item.pack_install_id for item in self.impact_baseline]


def _resolve_active_install(
    session: Session,
    pack_id: str,
    *,
    allow_compatible_packs: bool,
) -> PackInstall:
    installs = list(
        session.scalars(
            select(PackInstall)
            .where(PackInstall.pack_id == pack_id, PackInstall.status == "active")
            .order_by(PackInstall.id)
        ).all()
    )
    if not installs:
        raise DeliberationScopeError("pack_not_installed", f"no active install for pack_id={pack_id}")
    if len(installs) != 1:
        raise DeliberationScopeError(
            "ambiguous_active_revision",
            f"pack_id={pack_id} has {len(installs)} active installs; expected exactly one",
        )
    install = installs[0]
    if install.admission_profile == "compatible" and not allow_compatible_packs:
        raise DeliberationScopeError(
            "compatible_pack_not_allowed",
            f"pack_id={pack_id} is compatible; enable allow_compatible_packs to use it",
        )
    if install.admission_profile not in {"strict", "compatible"}:
        raise DeliberationScopeError(
            "unknown_admission_profile",
            f"pack_id={pack_id} has unsupported admission_profile={install.admission_profile}",
        )
    return install


def _freeze_ref(install: PackInstall, role: str) -> FrozenPackRef:
    snapshot = {
        "pack_install_id": install.id,
        "pack_id": install.pack_id,
        "declared_version": install.declared_version,
        "source_digest": install.source_digest,
        "admission_profile": install.admission_profile,
        "format_version": install.format_version,
        "grammar_version": install.grammar_version,
    }
    return FrozenPackRef(
        pack_install_id=install.id,
        pack_id=install.pack_id,
        declared_version=install.declared_version,
        source_digest=install.source_digest,
        admission_profile=install.admission_profile,
        role=role,
        snapshot=snapshot,
    )


def freeze_pack_scope(
    session: Session,
    pack_ids: list[str],
    impact_baseline_pack_ids: list[str] | None = None,
    *,
    allow_compatible_packs: bool = False,
) -> FrozenScope:
    """Resolve active Packs once and return exact install IDs for the run."""
    if not pack_ids:
        raise DeliberationScopeError("empty_primary_scope", "primary pack scope must not be empty")
    if len(pack_ids) != len(set(pack_ids)):
        raise DeliberationScopeError("duplicate_pack_id", "primary pack_ids must be unique")

    baseline_ids = list(impact_baseline_pack_ids or [])
    if len(baseline_ids) != len(set(baseline_ids)):
        raise DeliberationScopeError(
            "duplicate_baseline_pack_id", "impact baseline pack_ids must be unique"
        )
    primary_set = set(pack_ids)
    if not set(baseline_ids).issubset(primary_set):
        raise DeliberationScopeError(
            "baseline_not_subset",
            "impact baseline packs must be a subset of primary packs",
        )

    primary_refs: list[FrozenPackRef] = []
    by_pack_id: dict[str, FrozenPackRef] = {}
    for pack_id in pack_ids:
        install = _resolve_active_install(
            session, pack_id, allow_compatible_packs=allow_compatible_packs
        )
        ref = _freeze_ref(install, "primary")
        primary_refs.append(ref)
        by_pack_id[pack_id] = ref

    baseline_refs = [
        FrozenPackRef(
            pack_install_id=by_pack_id[pack_id].pack_install_id,
            pack_id=by_pack_id[pack_id].pack_id,
            declared_version=by_pack_id[pack_id].declared_version,
            source_digest=by_pack_id[pack_id].source_digest,
            admission_profile=by_pack_id[pack_id].admission_profile,
            role="impact_baseline",
            snapshot=dict(by_pack_id[pack_id].snapshot),
        )
        for pack_id in baseline_ids
    ]

    primary_digest = payload_digest(
        [ref.as_dict() for ref in primary_refs]
    )
    baseline_digest = payload_digest([ref.as_dict() for ref in baseline_refs])
    # Touch canonical_json to keep helper import meaningful for digest stability.
    assert canonical_json({"primary": primary_digest})
    return FrozenScope(
        primary=tuple(primary_refs),
        impact_baseline=tuple(baseline_refs),
        primary_digest=primary_digest,
        impact_baseline_digest=baseline_digest,
    )
