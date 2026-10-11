"""A deterministic stand-in for the LLM provider and the embedding model (design §13.4, N2).

Speaks the OpenAI-compatible endpoints the engine uses, so the product runs unchanged against it:

- GET  /v1/models            the engine's health check
- POST /v1/chat/completions  SQL generation (a fixed query per known question) and the summary
- POST /v1/embeddings        deterministic unit vectors derived from SHA-256 of each text

Standard library only. Not part of the product: used by docker-compose.e2e.yml.
"""

import hashlib
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "stub-model"
EMBED_DIM = 16

# Question (lower-cased, contains) -> SQL the "model" writes. Valid on the seeded Northwind DB.
ANSWERS = {
    "spent the most last month": """\
SELECT c.company_name,
       ROUND(SUM(od.unit_price * od.quantity * (1 - od.discount))::numeric, 2) AS total_spent
FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
JOIN order_details od ON od.order_id = o.order_id
WHERE o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY c.company_name
ORDER BY total_spent DESC
LIMIT 5""",
}
FALLBACK_SQL = "SELECT count(*) AS customers FROM customers"
SUMMARY = "Ernst Handel spent the most last month, followed by Save-a-lot Markets."


def question_of(messages: list[dict[str, str]]) -> str:
    """The text between <question> tags in the last user message (design §8.1)."""
    user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    start, end = user.rfind("<question>"), user.rfind("</question>")
    return user[start + len("<question>") : end].strip() if 0 <= start < end else user


def reply_for(messages: list[dict[str, str]]) -> str:
    system = messages[0]["content"] if messages else ""
    if system.startswith("You summarise SQL query results"):  # design §8.3
        return SUMMARY
    question = question_of(messages).lower()
    sql = next((s for key, s in ANSWERS.items() if key in question), FALLBACK_SQL)
    return f"```sql\n{sql}\n```"


def embedding(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [(b - 127.5) / 127.5 for b in digest[:EMBED_DIM]]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, body: object) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path in ("/v1/models", "/health"):
            self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        body = self._body()
        if self.path == "/v1/chat/completions":
            messages = body.get("messages") or []
            text = reply_for(messages)  # type: ignore[arg-type]
            self._json(200, {
                "id": "stub-1",
                "object": "chat.completion",
                "model": MODEL,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            })
        elif self.path == "/v1/embeddings":
            inputs = body.get("input") or []
            texts = [inputs] if isinstance(inputs, str) else list(inputs)  # type: ignore[arg-type]
            self._json(200, {
                "object": "list",
                "model": body.get("model"),
                "data": [{"object": "embedding", "index": i, "embedding": embedding(t)}
                         for i, t in enumerate(texts)],
            })
        else:
            self._json(404, {"error": {"message": "not found"}})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"stub-llm: {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
