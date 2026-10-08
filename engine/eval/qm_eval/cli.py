"""`qm-eval` command line (design §10.3).

    qm-eval run (--subset N | --full) [--seed 42] [--tag NAME] [--timeout-ms 30000]
                [--resume RUN_DIR] [--data-root DIR] [--manifest FILE] [--runs-dir DIR]

Provider, model, reasoning effort and pacing come from the same environment / .env keys as
the product (design E9, E11). `--linking`, `--few-shot` and `--self-correction` are added with
their features (T22-T24); `compare` and `cost` with T25 and T18.

Exit codes: 0 finished, 1 refused (manifest or resume mismatch), 2 usage error,
75 interrupted by an LLM rate limit or outage (continue with --resume).
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from qm_engine.config import EngineConfig
from qm_engine.llm.client import LLMClient, OpenAICompatibleClient
from qm_engine.observability import configure_logging
from qm_eval.runner import (
    DEFAULT_RUNS_DIR,
    EvalRunner,
    ResumeMismatch,
    RunInterrupted,
    RunSpec,
)
from qm_eval.spider import (
    DEFAULT_DATA_ROOT,
    DEFAULT_MANIFEST,
    ManifestError,
    load_split,
    sample_subset,
)

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 75


def make_config() -> EngineConfig:
    """Settings from the environment and .env (replaced in tests)."""
    return EngineConfig()


def make_llm(cfg: EngineConfig) -> LLMClient:
    return OpenAICompatibleClient(cfg)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qm-eval", description="QueryMind Spider evaluation")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the engine on Spider dev and score EX")
    size = run.add_mutually_exclusive_group(required=True)
    size.add_argument("--subset", type=int, metavar="N", help="fixed random subset of N questions")
    size.add_argument("--full", action="store_true", help="the full dev set (reported numbers)")
    run.add_argument("--seed", type=int, default=42, help="subset sampling seed (default 42)")
    run.add_argument("--split", choices=["dev"], default="dev")
    run.add_argument("--tag", default="run", help="becomes part of the run directory name")
    run.add_argument("--timeout-ms", type=int, default=30_000, help="per-query SQLite timeout")
    run.add_argument("--resume", type=Path, metavar="RUN_DIR", help="continue an interrupted run")
    run.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    return parser


def _spec(args: argparse.Namespace) -> RunSpec:
    ids = [ex.question_id for ex in load_split(args.data_root, args.split)]
    chosen = ids if args.full else sample_subset(ids, args.subset, args.seed)
    return RunSpec(
        data_root=args.data_root,
        manifest=args.manifest,
        question_ids=tuple(chosen),
        full=bool(args.full),
        seed=None if args.full else args.seed,
        tag=args.tag,
        timeout_ms=args.timeout_ms,
        split=args.split,
    )


async def _run(args: argparse.Namespace) -> int:
    cfg = make_config()
    llm = make_llm(cfg)
    try:
        runner = EvalRunner(_spec(args), cfg, llm, runs_dir=args.runs_dir)
        if args.resume is not None:
            runner.check_resumable(args.resume)
        run_dir = await runner.run(args.resume)
    finally:
        aclose = getattr(llm, "aclose", None)
        if aclose is not None:
            await aclose()

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    print(f"run dir: {run_dir}")
    print(
        f"EX {summary['ex']} ({summary['n_correct']}/{summary['n_scored']} scored, "
        f"{summary['n_gold_errors']} gold errors) | reportable: {summary['reportable']}"
    )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run" and args.subset is not None and args.subset <= 0:
        parser.error("--subset must be a positive integer")
    configure_logging(stream=sys.stderr)
    try:
        return asyncio.run(_run(args))
    except RunInterrupted as e:
        print(
            f"Interrupted at question {e.question_id} ({e.error.code}: {e.error.message}). "
            f"Nothing was recorded for it. Continue later with the same arguments plus:\n"
            f"  --resume {e.run_dir}",
            file=sys.stderr,
        )
        return EXIT_INTERRUPTED
    except (ManifestError, ResumeMismatch) as e:
        print(f"Refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
