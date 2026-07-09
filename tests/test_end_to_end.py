from __future__ import annotations

from sqlalchemy import func, select

from openoyster.loops.supervisor import Supervisor
from openoyster.models import (
    Artifact,
    Document,
    Entity,
    Evaluation,
    Hypothesis,
    LoopRun,
    Run,
    Signal,
    Task,
)


def test_supervisor_end_to_end(temp_settings, session_factory):
    (temp_settings.inbox_dir / "strategy.md").write_text(
        """
        Acme is hiring data platform engineers and launching an AI automation product.
        The CEO says the strategic bottleneck is data governance rather than model quality.
        Enterprise customers report permission metadata gaps and delayed approvals.
        """,
        encoding="utf-8",
    )
    (temp_settings.inbox_dir / "counterpoint.md").write_text(
        """
        Acme's audit team found no evidence that model quality is the primary blocker.
        A new permission service is being deployed, although customer adoption remains uncertain.
        """,
        encoding="utf-8",
    )
    supervisor = Supervisor(session_factory=session_factory, settings=temp_settings)
    supervisor.run_cycles(cycles=4)

    with session_factory() as session:
        assert session.scalar(select(func.count(Document.id))) == 2
        assert session.scalar(select(func.count(Signal.id))) >= 2
        assert session.scalar(select(func.count(Hypothesis.id))) >= 1
        assert session.scalar(select(func.count(Task.id))) >= 1
        assert session.scalar(select(func.count(Run.id)).where(Run.success.is_(True))) >= 1
        assert session.scalar(select(func.count(Artifact.id))) >= 1
        assert session.scalar(select(func.count(Evaluation.id))) >= 1
        assert session.scalar(select(func.count(LoopRun.id)).where(LoopRun.status == "failed")) == 0


def test_korean_only_document_end_to_end_with_stub(temp_settings, session_factory):
    (temp_settings.inbox_dir / "korean.md").write_text(
        """
        오픈오이스터는 병원 데이터 거버넌스 절차를 정비했다.
        감사 로그와 승인 이력이 저장되며, 현장 배포 일정은 위원회 검토 이후로 조정됐다.
        """,
        encoding="utf-8",
    )
    supervisor = Supervisor(session_factory=session_factory, settings=temp_settings)
    supervisor.run_cycles(cycles=4)

    with session_factory() as session:
        assert session.scalar(select(func.count(Document.id))) == 1
        assert session.scalar(select(func.count(Entity.id))) >= 1
        assert session.scalar(select(func.count(Signal.id))) >= 1
        assert session.scalar(select(func.count(LoopRun.id)).where(LoopRun.status == "failed")) == 0
