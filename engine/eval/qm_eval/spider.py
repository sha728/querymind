"""Spider 1.0 data: loading, the SHA-256 manifest check, and subset sampling (design §10.1, §10.3).

Layout under the data root (as shipped in spider_data.zip)::

    dev.json  train_spider.json  tables.json  dev_gold.sql
    database/<db_id>/<db_id>.sqlite

Question IDs are positions in ``dev.json`` (Spider has no IDs of its own).
"""

import hashlib
import json
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Split = Literal["dev", "train"]
SPLIT_FILES: dict[Split, str] = {"dev": "dev.json", "train": "train_spider.json"}

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "spider"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "spider_manifest.sha256"


class ManifestError(Exception):
    """The Spider files on disk do not match the committed manifest."""


@dataclass(frozen=True)
class SpiderExample:
    question_id: int
    db_id: str
    question: str
    query: str  # gold SQL


def load_split(root: Path, split: Split) -> list[SpiderExample]:
    path = root / SPLIT_FILES[split]
    with path.open(encoding="utf-8") as f:
        items = json.load(f)
    return [
        SpiderExample(question_id=i, db_id=it["db_id"], question=it["question"], query=it["query"])
        for i, it in enumerate(items)
    ]


def db_path(root: Path, db_id: str) -> Path:
    return root / "database" / db_id / f"{db_id}.sqlite"


def sample_subset(ids: Iterable[int], n: int, seed: int) -> list[int]:
    """``random.Random(seed).sample`` over the sorted IDs; returned sorted (design §10.3)."""
    pool = sorted(ids)
    if not 0 < n <= len(pool):
        raise ValueError(f"subset size {n} must be between 1 and {len(pool)}")
    return sorted(random.Random(seed).sample(pool, n))


def subset_hash(question_ids: Sequence[int]) -> str:
    """Hash of the exact question-ID list a run uses (recorded in config.json)."""
    return hashlib.sha256(",".join(map(str, question_ids)).encode()).hexdigest()


# --- manifest -------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_files(root: Path) -> list[str]:
    """Files covered by the manifest: the split/schema/gold files and every dev database."""
    names = ["dev.json", "train_spider.json", "tables.json", "dev_gold.sql"]
    dev_dbs = sorted({ex.db_id for ex in load_split(root, "dev")})
    names += [db_path(root, d).relative_to(root).as_posix() for d in dev_dbs]
    return names


def write_manifest(root: Path, manifest: Path) -> int:
    """Write ``<sha256>  <relative path>`` lines (sha256sum format). Returns the file count."""
    lines = [f"{_sha256(root / name)}  {name}" for name in manifest_files(root)]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(lines)


def manifest_hash(manifest: Path) -> str:
    return _sha256(manifest)


def verify_manifest(root: Path, manifest: Path) -> None:
    """Raise ``ManifestError`` naming every missing or changed file."""
    if not manifest.is_file():
        raise ManifestError(f"Manifest not found: {manifest}")
    if not root.is_dir():
        raise ManifestError(
            f"Spider data not found at {root}. Download Spider 1.0 and extract it there "
            "(see engine/eval/README.md)."
        )
    problems = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, name = line.split(maxsplit=1)
        path = root / name
        if not path.is_file():
            problems.append(f"missing: {name}")
        elif _sha256(path) != expected:
            problems.append(f"changed: {name}")
    if problems:
        raise ManifestError(
            "Spider data does not match the manifest "
            f"({len(problems)} problem(s)): " + "; ".join(problems[:10])
        )
