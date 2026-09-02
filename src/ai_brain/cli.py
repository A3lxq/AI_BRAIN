"""AI_BRAIN command-line entry point.

Per `docs/ROADMAP.md` Phase 1: a minimal CLI plus the `doctor` diagnostics
command. Subcommands grow here as later phases add real functionality;
nothing here talks to the vault/Qdrant/Huey directly — it only wires
`ai_brain.config`/`ai_brain.diagnostics` together for a human at a terminal.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from ai_brain import __version__
from ai_brain.config import load_config
from ai_brain.diagnostics import DoctorReport, run_doctor
from ai_brain.logging_setup import configure_logging

_STATUS_SYMBOL = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]"}


def _print_doctor_report(report: DoctorReport) -> None:
    for check in report.checks:
        print(f"{_STATUS_SYMBOL[check.status]} {check.name}: {check.message}")
    print()
    print(f"Overall: {report.overall}")


def _cmd_doctor(_args: argparse.Namespace) -> int:
    config = load_config()
    report = run_doctor(config)
    _print_doctor_report(report)
    return report.exit_code


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"ai-brain {__version__}")
    return 0


def _cmd_migrate(_args: argparse.Namespace) -> int:
    # Imported lazily: aiosqlite-only, no huey/vault requirement, but keeping
    # it out of the top-level import list matches this module's existing
    # policy of not pulling in a subcommand's dependencies until it's used.
    from ai_brain.db.connection import open_connection
    from ai_brain.db.migrate import (
        DEFAULT_MIGRATIONS_DIR,
        MigrationError,
        MigrationRecord,
        apply_pending_migrations,
    )

    config = load_config()

    async def _run() -> list[MigrationRecord]:
        async with open_connection(config.db_path) as conn:
            return await apply_pending_migrations(conn, DEFAULT_MIGRATIONS_DIR)

    try:
        records = asyncio.run(_run())
    except MigrationError as exc:
        print(f"[FAIL] {exc}")
        return 1

    for record in records:
        print(f"[ok]   {record.filename} (version {record.version}, applied {record.applied_at})")
    print(f"\nSchema at version {records[-1].version if records else 0}.")
    return 0


def _cmd_ingest_bootstrap(_args: argparse.Namespace) -> int:
    # ai_brain.worker constructs a Huey instance and hard-fails at import
    # time if AI_BRAIN_HUEY_SECRET is misconfigured (design doc §2.10) --
    # imported here, not at module top level, so `doctor`/`version`/`migrate`
    # never require a Huey secret just to run.
    from ai_brain.worker import run_bootstrap

    summary = run_bootstrap()
    print(f"notes_ingested={summary.notes_ingested} notes_skipped={summary.notes_skipped} ")
    print(f"notes_failed={summary.notes_failed} duration_ms={summary.duration_ms}")
    print(f"outcome_counts={summary.outcome_counts}")
    return 1 if summary.notes_failed else 0


def _cmd_ingest_reconcile(_args: argparse.Namespace) -> int:
    from ai_brain.worker import run_reconcile

    summary = run_reconcile()
    print(
        f"paths_scanned={summary.paths_scanned} "
        f"discrepancies_found={summary.discrepancies_found} "
        f"jobs_enqueued={summary.jobs_enqueued} duration_ms={summary.duration_ms}"
    )
    return 0


def _cmd_index_bootstrap(_args: argparse.Namespace) -> int:
    from ai_brain.worker import run_index_bootstrap

    # Unlike ingest bootstrap/reconcile, this command has no meaningful
    # metadata-only fallback -- it IS the indexing command -- so a Qdrant
    # connection failure is reported as a clean CLI error, not degraded
    # silently or left to leak a raw traceback (design doc §6/§8).
    try:
        summary = run_index_bootstrap()
    except Exception as exc:
        print(f"[FAIL] unable to reach Qdrant: {exc}")
        return 1
    print(f"notes_indexed={summary.notes_indexed} notes_failed={summary.notes_failed}")
    return 1 if summary.notes_failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-brain", description="AI_BRAIN CLI")
    parser.add_argument(
        "--log-level",
        default=None,
        help="override AI_BRAIN_LOG_LEVEL for this invocation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="run diagnostics")
    doctor_parser.set_defaults(func=_cmd_doctor)

    version_parser = subparsers.add_parser("version", help="print the AI_BRAIN version")
    version_parser.set_defaults(func=_cmd_version)

    migrate_parser = subparsers.add_parser("migrate", help="apply pending database migrations")
    migrate_parser.set_defaults(func=_cmd_migrate)

    ingest_parser = subparsers.add_parser("ingest", help="vault ingestion commands")
    ingest_subparsers = ingest_parser.add_subparsers(dest="ingest_command", required=True)

    bootstrap_parser = ingest_subparsers.add_parser(
        "bootstrap", help="one-time full-vault ingestion"
    )
    bootstrap_parser.set_defaults(func=_cmd_ingest_bootstrap)

    reconcile_parser = ingest_subparsers.add_parser(
        "reconcile", help="run one on-demand reconciliation pass"
    )
    reconcile_parser.set_defaults(func=_cmd_ingest_reconcile)

    index_parser = subparsers.add_parser("index", help="indexing pipeline commands")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)

    index_bootstrap_parser = index_subparsers.add_parser(
        "bootstrap", help="index every note not currently indexed"
    )
    index_bootstrap_parser.set_defaults(func=_cmd_index_bootstrap)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config()
    configure_logging(args.log_level or config.log_level)

    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
