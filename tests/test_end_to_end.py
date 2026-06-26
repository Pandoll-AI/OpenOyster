from __future__ import annotations

from sqlalchemy import func, select

from openoyster.loops.supervisor import Supervisor
from openoyster.models import (
    Artifact,
    Document,
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
