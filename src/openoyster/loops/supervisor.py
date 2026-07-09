from __future__ import annotations

import os
import socket
import time
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..database import init_db, make_engine, make_session_factory
from ..events import bus
from ..models import LoopRun
from ..policies import ensure_default_mission, ensure_default_policy
from .base import BaseLoop, LoopResult
from .evaluation import EvaluationLoop
from .execution import ExecutionLoop
from .extraction import ExtractionLoop
from .hypothesis import HypothesisLoop
from .intake import DocumentIntakeLoop
from .maintenance import MaintenanceLoop
from .planning import PlanningLoop
from .utilisation import UtilisationLoop


class Supervisor:
    """Runs loop workers with independent transactions, durable leases, and run telemetry."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        settings: Settings | None = None,
        loops: list[BaseLoop] | None = None,
    ):
        self.settings = settings or get_settings()
        self._owned_engine: Engine | None = None
        if session_factory is None:
            self._owned_engine = make_engine(self.settings)
            init_db(self._owned_engine)
            session_factory = make_session_factory(self._owned_engine)
        self.session_factory = session_factory
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        self.loops = loops or [
            DocumentIntakeLoop(self.settings),
            MaintenanceLoop(self.settings),
            ExtractionLoop(self.settings),
            HypothesisLoop(self.settings),
            PlanningLoop(self.settings),
            ExecutionLoop(self.settings),
            UtilisationLoop(self.settings),
            EvaluationLoop(self.settings),
        ]

    def close(self) -> None:
        if self._owned_engine is not None:
            self._owned_engine.dispose()
            self._owned_engine = None

    def initialise(self) -> None:
        with self.session_factory() as session:
            ensure_default_policy(session, self.settings)
            ensure_default_mission(session)
            session.commit()

    def _start_run(self, loop: BaseLoop) -> int | None:
        with self.session_factory() as session:
            if not bus.acquire_lease(
                session,
                loop_name=loop.name,
                owner=self.owner,
                ttl_seconds=self.settings.loop_lease_seconds,
            ):
                session.rollback()
                return None
            run = LoopRun(
                loop_name=loop.name,
                owner=self.owner,
                status="running",
            )
            session.add(run)
            session.commit()
            return run.id

    def _finish_run(
        self,
        *,
        loop: BaseLoop,
        run_id: int,
        result: LoopResult | None,
        started: float,
        error: Exception | None = None,
    ) -> None:
        with self.session_factory() as session:
            run = session.get(LoopRun, run_id)
            if run:
                run.status = "failed" if error else "completed"
                run.error = str(error) if error else None
                if result:
                    run.consumed_events = result.consumed_events
                    run.emitted_events = result.emitted_events
                    run.created_records_json = result.created_records
                    run.notes_json = result.notes
                run.finished_at = datetime.now(UTC)
                run.duration_ms = (time.perf_counter() - started) * 1000
            bus.release_lease(session, loop_name=loop.name, owner=self.owner)
            session.commit()

    def run_cycle(self, limit: int | None = None) -> list[LoopResult]:
        self.initialise()
        event_limit = limit or self.settings.max_events_per_loop
        results: list[LoopResult] = []
        for loop in self.loops:
            run_id = self._start_run(loop)
            if run_id is None:
                results.append(
                    LoopResult(loop_name=loop.name, notes=["Skipped because another worker owns the lease."])
                )
                continue
            started = time.perf_counter()
            result: LoopResult | None = None
            try:
                with self.session_factory() as session:
                    result = loop.run(session, limit=event_limit)
                    session.commit()
                results.append(result)
                self._finish_run(
                    loop=loop,
                    run_id=run_id,
                    result=result,
                    started=started,
                )
            except Exception as exc:
                failed_result = LoopResult(loop_name=loop.name, notes=[f"FAILED: {exc}"])
                results.append(failed_result)
                self._finish_run(
                    loop=loop,
                    run_id=run_id,
                    result=failed_result,
                    started=started,
                    error=exc,
                )
                if not self.settings.continue_on_loop_error:
                    raise
        return results

    def run_cycles(
        self,
        cycles: int = 1,
        sleep_seconds: float = 0.0,
    ) -> list[list[LoopResult]]:
        all_results: list[list[LoopResult]] = []
        for index in range(cycles):
            all_results.append(self.run_cycle())
            if sleep_seconds and index < cycles - 1:
                time.sleep(sleep_seconds)
        return all_results

    @staticmethod
    def serialise(results: Iterable[LoopResult]) -> list[dict]:
        return [asdict(result) for result in results]
