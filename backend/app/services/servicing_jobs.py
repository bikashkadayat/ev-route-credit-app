"""Nightly servicing jobs — TRD §2.10.

The scheduler is not implemented here; these are the callable, idempotent units the
scheduler will eventually invoke, and the same functions an operator triggers by hand.

Every job is a pure function of ``(stored state, as_of date)`` wrapped in a ``job_runs``
row, so re-running one for the same business date produces the same result and leaves no
second effect. That is what makes a retry safe after a partial night.

A ``job_runs`` row is written only when a job actually executes. Nothing here fabricates a
success for work that did not run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.observability import job_last_success_timestamp
from app.models.system import JobRun
from app.repositories.alert import LoanRepository
from app.repositories.loan import JobRunRepository, LoanBookRepository
from app.services.alert import AlertService
from app.services.loan import LoanService
from app.services.telemetry import TelemetryService

TELEMETRY_INGEST = "ingest_telemetry"
BASELINE_REBUILD = "rebuild_usage_baselines"
DPD_UPDATE = "recompute_dpd_and_outstanding"
CLASSIFICATION_UPDATE = "recompute_classification"
ALERT_EVALUATION = "evaluate_risk_rules"


@dataclass(frozen=True, slots=True)
class JobResult:
    job_name: str
    as_of: date
    status: str
    rows_processed: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_name": self.job_name,
            "as_of": self.as_of.isoformat(),
            "status": self.status,
            "rows_processed": self.rows_processed,
            "error": self.error,
        }


class ServicingJobService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.loans = LoanBookRepository(session)
        self.job_runs = JobRunRepository(session)
        self.loan_reads = LoanRepository(session)
        self.loan_service = LoanService(session)
        self.alerts = AlertService(session)
        self.telemetry = TelemetryService(session)

    # -- job-run bookkeeping ----------------------------------------------
    @contextmanager
    def _tracked(self, job_name: str, as_of: date) -> Iterator[dict[str, Any]]:
        """Wrap one execution in a ``job_runs`` row.

        The row is created when the job starts and finished either way, so a job that dies
        halfway leaves a FAILED record rather than silence — a silently missing night is
        the failure mode the ``job_last_success_timestamp`` alert exists to catch.
        """
        started = datetime.now(UTC)

        # ``uq_job_success`` permits exactly one SUCCESS row per (job, business date), so
        # a re-run updates that row rather than inserting a second one. The work itself is
        # still done: the recomputation is idempotent, and a loan booked after the nightly
        # pass has to be picked up when the pass is run again for the same date.
        run = self.job_runs.successful_run(job_name, as_of)
        if run is None:
            run = self.job_runs.start(
                job_name=job_name,
                started_at=started,
                status="RUNNING",
                records_processed=0,
                as_of_date=as_of,
            )
        else:
            run.started_at = started
            run.finished_at = None
            run.records_processed = 0
            run.error_message = None
        self.session.flush()

        state: dict[str, Any] = {"processed": 0}
        try:
            yield state
        except Exception as exc:
            run.status = "FAILED"
            run.finished_at = datetime.now(UTC)
            run.records_processed = int(state.get("processed", 0))
            run.error_message = str(exc).splitlines()[0][:500]
            self.session.flush()
            raise
        else:
            run.status = "SUCCESS"
            run.finished_at = datetime.now(UTC)
            run.records_processed = int(state.get("processed", 0))
            self.session.flush()
            job_last_success_timestamp.labels(job_name).set(
                run.finished_at.timestamp()
            )

    # -- jobs --------------------------------------------------------------
    def run_telemetry_ingest(self, *, as_of: date | None = None) -> JobResult:
        """FR-6.1, FR-6.2 — pull one day of aggregates for every financed vehicle.

        Idempotent by primary key: re-running for the same day overwrites rather than
        double-counts.
        """
        business_date = as_of or datetime.now(UTC).date()
        with self._tracked(TELEMETRY_INGEST, business_date) as state:
            result = self.telemetry.ingest_day(as_of=business_date)
            state["processed"] = result.telemetry_rows
        return JobResult(TELEMETRY_INGEST, business_date, "SUCCESS", state["processed"])

    def run_baseline_rebuild(self, *, as_of: date | None = None) -> JobResult:
        """FR-6.3, FR-6.4, FR-6.6 — recompute the snapshot every rule reads."""
        business_date = as_of or datetime.now(UTC).date()
        with self._tracked(BASELINE_REBUILD, business_date) as state:
            state["processed"] = self.telemetry.rebuild_snapshots(as_of=business_date)
        return JobResult(BASELINE_REBUILD, business_date, "SUCCESS", state["processed"])

    def run_dpd_update(self, *, as_of: date | None = None) -> JobResult:
        """Recompute days past due and outstanding for every active loan.

        Idempotent: the position is derived from the schedule, so a second run for the same
        business date writes the same numbers.
        """
        business_date = as_of or datetime.now(UTC).date()
        with self._tracked(DPD_UPDATE, business_date) as state:
            for loan_id in self.loans.active_loan_ids():
                loan = self.loan_service.get(loan_id)
                self.loan_service.refresh_position(loan, business_date)
                state["processed"] += 1
        return JobResult(DPD_UPDATE, business_date, "SUCCESS", state["processed"])

    #: Outstanding is recomputed in the same pass as DPD — they are two readings of one
    #: schedule, and splitting them would let the two disagree.
    run_outstanding_update = run_dpd_update

    def run_classification_update(self, *, as_of: date | None = None) -> JobResult:
        """Reclassify every active loan from its days past due (Doc 01 BR-4).

        Separate from the DPD job so an operator can reclassify without re-deriving
        arrears, but it reads the same stored DPD, so the two can never disagree.
        """
        business_date = as_of or datetime.now(UTC).date()
        with self._tracked(CLASSIFICATION_UPDATE, business_date) as state:
            from app.engines.servicing import classify, risk_status

            for loan_id in self.loans.active_loan_ids():
                loan = self.loan_service.get(loan_id)
                dpd = int(loan.days_past_due or 0)
                loan.classification = classify(dpd)
                loan.risk_status = risk_status(dpd)
                state["processed"] += 1
            self.session.flush()
        return JobResult(
            CLASSIFICATION_UPDATE, business_date, "SUCCESS", state["processed"]
        )

    def run_alert_evaluation(self, *, as_of: date | None = None) -> JobResult:
        """Evaluate the 22-rule catalogue over the active book.

        Delegates to ``AlertService`` unchanged: deduplication, supersession and
        auto-resolution are its responsibility, and this job adds only the run record.
        """
        business_date = as_of or datetime.now(UTC).date()
        with self._tracked(ALERT_EVALUATION, business_date) as state:
            run = self.alerts.evaluate(as_of=business_date)
            state["processed"] = run.loans_evaluated
        return JobResult(ALERT_EVALUATION, business_date, "SUCCESS", state["processed"])

    def run_nightly(self, *, as_of: date | None = None) -> list[JobResult]:
        """The documented nightly order: arrears first, then classification, then alerts.

        The order is not cosmetic. Telemetry lands first, then the baselines derived from
        it, then arrears, then classification, and only then the rules — the rule engine
        reads all of those, so evaluating earlier would raise alerts against yesterday's
        position.
        """
        business_date = as_of or datetime.now(UTC).date()
        return [
            self.run_telemetry_ingest(as_of=business_date),
            self.run_baseline_rebuild(as_of=business_date),
            self.run_dpd_update(as_of=business_date),
            self.run_classification_update(as_of=business_date),
            self.run_alert_evaluation(as_of=business_date),
        ]

    # -- history -----------------------------------------------------------
    def recent_runs(self, *, limit: int = 20) -> list[JobRun]:
        return list(self.job_runs.recent(limit=limit))


JOBS: dict[str, str] = {
    TELEMETRY_INGEST: "Ingest one day of telemetry and battery aggregates",
    BASELINE_REBUILD: "Recompute usage baselines and the monitoring snapshot",
    DPD_UPDATE: "Recompute days past due and outstanding for every active loan",
    CLASSIFICATION_UPDATE: "Reclassify active loans from their days past due",
    ALERT_EVALUATION: "Evaluate the risk-rule catalogue over the active book",
}


def job_callable(service: ServicingJobService, job_name: str) -> Callable[..., JobResult]:
    mapping = {
        TELEMETRY_INGEST: service.run_telemetry_ingest,
        BASELINE_REBUILD: service.run_baseline_rebuild,
        DPD_UPDATE: service.run_dpd_update,
        CLASSIFICATION_UPDATE: service.run_classification_update,
        ALERT_EVALUATION: service.run_alert_evaluation,
    }
    return mapping[job_name]
