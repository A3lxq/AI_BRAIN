"""AI_BRAIN command-line entry point.

Per `docs/ROADMAP.md` Phase 1: a minimal CLI plus the `doctor` diagnostics
command. Subcommands grow here as later phases add real functionality;
nothing here talks to the vault/Qdrant/Huey directly — it only wires
`ai_brain.config`/`ai_brain.diagnostics` together for a human at a terminal.
"""

from __future__ import annotations

import argparse
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
