"""Cost per 1,000 questions from a run's measured token usage (design §11.4, M5, R10.5).

    cost_usd   = sum(prompt_tokens * input_price + completion_tokens * output_price) / 1e6
    per_1k_usd = cost_usd / n_with_usage * 1000

Tokens are never estimated: questions whose provider reported no usage are counted in
``n_missing_usage`` and left out of both sums. Figures are list-price estimates; runs on a
free tier are billed $0.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from qm_eval.runner import read_results

DEFAULT_PRICING = Path(__file__).resolve().parents[1] / "pricing.yaml"


@dataclass(frozen=True)
class Price:
    provider: str
    model: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    source_url: str
    retrieved_on: date


@dataclass(frozen=True)
class CostReport:
    provider: str
    model: str
    n_questions: int
    n_with_usage: int
    n_missing_usage: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None  # None for local Ollama (no API cost)
    per_1k_usd: float | None
    price: Price | None


def load_pricing(path: Path = DEFAULT_PRICING) -> list[Price]:
    with path.open(encoding="utf-8") as f:
        entries = yaml.safe_load(f)
    return [Price(**e) for e in entries]


def find_price(prices: list[Price], provider: str, model: str) -> Price:
    for p in prices:
        if p.provider == provider and p.model == model:
            return p
    raise KeyError(f"no list price for provider={provider!r} model={model!r} in pricing file")


def compute_cost(run_dir: Path, prices: list[Price]) -> CostReport:
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    provider, model = config["llm"]["provider"], config["llm"]["model"]
    rows = read_results(run_dir / "results.jsonl")
    known = [
        r
        for r in rows
        if isinstance(r["prompt_tokens"], int) and isinstance(r["completion_tokens"], int)
    ]
    prompt = sum(int(r["prompt_tokens"]) for r in known)  # type: ignore[call-overload]
    completion = sum(int(r["completion_tokens"]) for r in known)  # type: ignore[call-overload]

    price = None if provider == "ollama" else find_price(prices, provider, model)
    cost = per_1k = None
    if price is not None:
        cost = (prompt * price.input_usd_per_mtok + completion * price.output_usd_per_mtok) / 1e6
        per_1k = cost / len(known) * 1000 if known else None
    return CostReport(
        provider=provider,
        model=model,
        n_questions=len(rows),
        n_with_usage=len(known),
        n_missing_usage=len(rows) - len(known),
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
        per_1k_usd=per_1k,
        price=price,
    )


def format_report(r: CostReport) -> str:
    lines = [
        f"provider/model: {r.provider} / {r.model}",
        f"questions: {r.n_questions} ({r.n_with_usage} with usage, "
        f"{r.n_missing_usage} missing usage, not estimated)",
        f"tokens: prompt {r.prompt_tokens:,}, completion {r.completion_tokens:,} "
        "(completion includes reasoning tokens)",
    ]
    if r.price is None:
        lines.append("cost: $0 API cost (local hardware)")
    elif r.cost_usd is None or r.per_1k_usd is None:
        lines.append("cost: n/a (no question reported token usage)")
    else:
        p = r.price
        lines += [
            f"cost: ${r.cost_usd:.4f} total, ${r.per_1k_usd:.4f} per 1,000 questions "
            f"- estimate at list price retrieved on {p.retrieved_on.isoformat()}",
            f"list price: ${p.input_usd_per_mtok}/M input, ${p.output_usd_per_mtok}/M output "
            f"({p.source_url}); runs on the free tier are billed $0",
        ]
    return "\n".join(lines)
