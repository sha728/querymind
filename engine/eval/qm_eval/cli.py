"""`qm-eval` command line (design §10.3).

    qm-eval run (--subset N | --full) [--seed 42] [--tag NAME] [--timeout-ms 30000]
                [--resume RUN_DIR] [--data-root DIR] [--manifest FILE] [--runs-dir DIR]
    qm-eval cost RUN_DIR [--pricing FILE]
    qm-eval compare RUN_A RUN_B

Provider, model, reasoning effort and pacing come from the same environment / .env keys as
the product (design E9, E11). `--linking`, `--few-shot` and `--self-correction` are added with
their features (T22-T24).

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
from qm_engine.execution.postgres import use_selector_event_loop_on_windows
from qm_engine.llm.client import LLMClient, OpenAICompatibleClient
from qm_engine.llm.embeddings import Embedder, OllamaEmbedder
from qm_engine.observability import configure_logging
from qm_eval.compare import compare_runs, format_comparison
from qm_eval.cost import DEFAULT_PRICING, compute_cost, format_report, load_pricing
from qm_eval.runner import (
    DEFAULT_RUNS_DIR,
    EvalRunner,
    ResumeMismatch,
    RunInterrupted,
    RunLocked,
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


def make_embedder(cfg: EngineConfig) -> Embedder:
    return OllamaEmbedder(cfg)


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
    run.add_argument(
        "--linking",
        choices=["off", "on", "auto"],
        default="off",
        help="schema linking (design 6.3); 'on' forces it for ablations (default off)",
    )
    run.add_argument(
        "--few-shot",
        choices=["off", "on"],
        default="off",
        help="similar Spider-train examples in the prompt (design 6.4; default off)",
    )
    run.add_argument(
        "--self-correction",
        choices=["off", "on"],
        default="off",
        help="retry failed queries with the error (design 6.2 step 9; default off)",
    )
    run.add_argument(
        "--max-corrections",
        type=int,
        default=2,
        metavar="N",
        help="correction attempts after the first generation (default 2)",
    )
    run.add_argument(
        "--linking-top-k",
        type=int,
        default=5,
        metavar="K",
        help="tables kept by linking before FK bridging (default 5; A1/A4 use 3)",
    )
    run.add_argument("--resume", type=Path, metavar="RUN_DIR", help="continue an interrupted run")
    run.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)

    cost = sub.add_parser("cost", help="token totals and list-price cost per 1,000 questions")
    cost.add_argument("run_dir", type=Path)
    cost.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)

    cmp_ = sub.add_parser("compare", help="paired comparison of two runs (exact McNemar)")
    cmp_.add_argument("run_a", type=Path, help="baseline run folder (e.g. A0)")
    cmp_.add_argument("run_b", type=Path, help="run to compare against it")
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
        linking_mode=args.linking,
        linking_top_k=args.linking_top_k,
        few_shot=args.few_shot == "on",
        self_correction=args.self_correction == "on",
        max_corrections=args.max_corrections,
    )


async def _run(args: argparse.Namespace) -> int:
    cfg = make_config()
    llm = make_llm(cfg)
    spec = _spec(args)
    needs_embedder = spec.linking_mode != "off" or spec.few_shot
    embedder = make_embedder(cfg) if needs_embedder else None
    try:
        runner = EvalRunner(spec, cfg, llm, runs_dir=args.runs_dir, embedder=embedder)
        if args.resume is not None:
            runner.check_resumable(args.resume)
        run_dir = await runner.run(args.resume)
    finally:
        for client in (llm, embedder):
            aclose = getattr(client, "aclose", None)
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
    if args.command == "run" and args.linking_top_k <= 0:
        parser.error("--linking-top-k must be a positive integer")
    if args.command == "run" and args.max_corrections < 0:
        parser.error("--max-corrections must be 0 or more")
    configure_logging(target="stderr")  # stdout carries the result
    use_selector_event_loop_on_windows()
    if args.command == "compare":
        for d in (args.run_a, args.run_b):
            if not (d / "results.jsonl").is_file():
                print(f"Refused: no results.jsonl in {d}", file=sys.stderr)
                return EXIT_REFUSED
        result = compare_runs(args.run_a, args.run_b)
        print(format_comparison(result, args.run_a.name, args.run_b.name))
        return EXIT_OK
    if args.command == "cost":
        try:
            print(format_report(compute_cost(args.run_dir, load_pricing(args.pricing))))
        except (KeyError, FileNotFoundError) as e:
            print(f"Refused: {e}", file=sys.stderr)
            return EXIT_REFUSED
        return EXIT_OK
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
    except (ManifestError, ResumeMismatch, RunLocked) as e:
        print(f"Refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
