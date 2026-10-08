"""T11 spike: does Ollama's OpenAI-compatible endpoint honour `options.num_ctx`?

Sends one large prompt (~12k tokens, longer than any context tested) through
`/v1/chat/completions` and reports the `prompt_tokens` Ollama actually used:

  1. raw request without `options`        -> the server's effective context
  2. raw request with options.num_ctx=16384 -> differs from (1) only if the option is honoured
  3. the engine's own client (no options, design E10)

and reports the loaded model's `context_length` from Ollama's `/api/ps`.

Results (2026-10-08, Ollama 0.40.0, CPU): with the server default every run reported
prompt_tokens=2050; with OLLAMA_CONTEXT_LENGTH=8192 every run reported 4098 while /api/ps
said 8192. `options.num_ctx` is ignored and requests are capped at about half the reported
context -> Ollama is local-dev only (design E10); reported runs use Cerebras (Q1 v0.3).

Run from engine/:  .venv/Scripts/python scripts/check_num_ctx.py
Requires a running Ollama with qwen2.5-coder:7b pulled. Reads OLLAMA settings from .env (E9).
"""

import asyncio
import json
import platform
import time

import httpx

from qm_engine.config import EngineConfig
from qm_engine.llm.client import Message, OpenAICompatibleClient

N_RECORDS = 1300  # ~12k tokens with the line format below: overflows every context tested
N_RECORDS_FITS = 550  # ~5-6k tokens: fits an 8192 context
QUESTION = "What is the record number of the FIRST record you can see? Reply with the number only."


def build_messages(n_records: int = N_RECORDS) -> list[Message]:
    lines = [f"Record {i}: the stored value is {i * 7} units." for i in range(1, n_records + 1)]
    return [
        {"role": "system", "content": "You answer questions about the records you are given."},
        {"role": "user", "content": "\n".join(lines) + "\n\n" + QUESTION},
    ]


async def raw_run(
    cfg: EngineConfig, messages: list[Message], num_ctx: int | None, label: str = "raw request"
) -> dict:
    body: dict[str, object] = {
        "model": cfg.llm_model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 16,
        "stream": False,
    }
    if num_ctx is not None:
        body["options"] = {"num_ctx": num_ctx}
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=900) as http:
        r = await http.post(f"{cfg.base_url.rstrip('/')}/chat/completions", json=body)
    r.raise_for_status()
    data = r.json()
    return {
        "via": label,
        "options_num_ctx": num_ctx,
        "prompt_tokens": data.get("usage", {}).get("prompt_tokens"),
        "answer": data["choices"][0]["message"]["content"].strip(),
        "seconds": round(time.perf_counter() - start, 1),
    }


async def client_run(cfg: EngineConfig, messages: list[Message]) -> dict:
    client = OpenAICompatibleClient(cfg.model_copy(update={"llm_timeout_s": 900.0}))
    start = time.perf_counter()
    try:
        result = await client.chat(messages, max_tokens=16, purpose="num_ctx_spike")
    finally:
        await client.aclose()
    return {
        "via": "engine client",
        "options_num_ctx": None,
        "prompt_tokens": result.prompt_tokens,
        "answer": result.text.strip(),
        "seconds": round(time.perf_counter() - start, 1),
    }


async def main() -> None:
    cfg = EngineConfig(llm_provider="ollama", llm_model="qwen2.5-coder:7b", llm_base_url=None)
    api = cfg.base_url.removesuffix("/").removesuffix("/v1")
    async with httpx.AsyncClient(timeout=10) as http:
        version = (await http.get(f"{api}/api/version")).json().get("version")
    messages = build_messages()

    runs = [
        await raw_run(cfg, messages, None),
        await raw_run(cfg, messages, 16384),
        await client_run(cfg, messages),
        await raw_run(cfg, build_messages(N_RECORDS_FITS), None, "raw request, prompt that fits"),
    ]
    async with httpx.AsyncClient(timeout=10) as http:
        ps = (await http.get(f"{api}/api/ps")).json().get("models", [])

    report = {
        "ollama_version": version,
        "model": cfg.llm_model,
        "endpoint": f"{cfg.base_url.rstrip('/')}/chat/completions",
        "host": platform.platform(),
        "prompt_chars": sum(len(m["content"]) for m in messages),
        "records_in_prompt": N_RECORDS,
        "records_in_fitting_prompt": N_RECORDS_FITS,
        "runs": runs,
        "options_num_ctx_honoured": runs[1]["prompt_tokens"] != runs[0]["prompt_tokens"],
        "api_ps": [{k: m.get(k) for k in ("name", "context_length", "size_vram")} for m in ps],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
