"""argparse CLI. SUBMIT_MODE defaults to dry-run; `real` is explicit."""
import argparse
import asyncio

import structlog


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    p = argparse.ArgumentParser(prog="applyme")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="apply to vacancies")
    src = run.add_mutually_exclusive_group(required=True)
    src.add_argument("--vacancies", type=str)
    src.add_argument("--url", type=str)
    run.add_argument("--profile", default="data/profile.json")
    run.add_argument("--submit-mode", choices=["dry-run", "sandbox", "real"], default="dry-run")
    run.add_argument("--headful", action="store_true", default=True)
    run.add_argument("--max-applies", type=int, default=5)
    return p


def main() -> None:
    """Entry point: configure logging, parse args, and run the apply command."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    args = build_parser().parse_args()
    from applyme.app import run_command  # wires config + engine + runner (Task 21)

    asyncio.run(run_command(args))
