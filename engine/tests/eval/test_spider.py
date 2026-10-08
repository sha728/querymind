import shutil
from pathlib import Path

import pytest

from qm_eval.spider import (
    ManifestError,
    db_path,
    load_split,
    manifest_files,
    sample_subset,
    subset_hash,
    verify_manifest,
    write_manifest,
)

FIXTURE = Path(__file__).parent / "fixtures" / "spider_mini"


@pytest.fixture
def data(tmp_path: Path) -> tuple[Path, Path]:
    """A writable copy of the fixture with a freshly written manifest."""
    root = tmp_path / "spider"
    shutil.copytree(FIXTURE, root)
    manifest = tmp_path / "spider_manifest.sha256"
    write_manifest(root, manifest)
    return root, manifest


def test_load_dev_split() -> None:
    dev = load_split(FIXTURE, "dev")
    assert [ex.question_id for ex in dev] == [0, 1, 2, 3, 4]
    assert dev[0].db_id == "mini"
    assert dev[0].question == "How many customers are there?"
    assert dev[0].query == "SELECT count(*) FROM customers"


def test_load_train_split() -> None:
    train = load_split(FIXTURE, "train")
    assert len(train) == 2
    assert train[1].query == "SELECT customer_id FROM customers"


def test_db_path_points_at_existing_sqlite() -> None:
    path = db_path(FIXTURE, "mini")
    assert path == FIXTURE / "database" / "mini" / "mini.sqlite"
    assert path.is_file()


# --- manifest ---


def test_manifest_covers_split_files_and_dev_databases(data: tuple[Path, Path]) -> None:
    root, manifest = data
    assert manifest_files(root) == [
        "dev.json",
        "train_spider.json",
        "tables.json",
        "dev_gold.sql",
        "database/mini/mini.sqlite",
    ]
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    digest, name = lines[0].split(maxsplit=1)
    assert len(digest) == 64 and name == "dev.json"


def test_untouched_data_passes(data: tuple[Path, Path]) -> None:
    verify_manifest(*data)


def test_tampered_file_fails_with_clear_error(data: tuple[Path, Path]) -> None:
    root, manifest = data
    gold = root / "dev_gold.sql"
    gold.write_text(gold.read_text(encoding="utf-8") + "SELECT 1\tmini\n", encoding="utf-8")
    with pytest.raises(ManifestError, match=r"changed: dev_gold\.sql"):
        verify_manifest(root, manifest)


def test_tampered_database_fails(data: tuple[Path, Path]) -> None:
    root, manifest = data
    with db_path(root, "mini").open("ab") as f:
        f.write(b"\0")
    with pytest.raises(ManifestError, match=r"changed: database/mini/mini\.sqlite"):
        verify_manifest(root, manifest)


def test_missing_file_fails(data: tuple[Path, Path]) -> None:
    root, manifest = data
    (root / "tables.json").unlink()
    with pytest.raises(ManifestError, match=r"missing: tables\.json"):
        verify_manifest(root, manifest)


def test_missing_data_dir_explains_download(tmp_path: Path, data: tuple[Path, Path]) -> None:
    _, manifest = data
    with pytest.raises(ManifestError, match=r"Download Spider 1.0"):
        verify_manifest(tmp_path / "nowhere", manifest)


# --- subset sampling ---


def test_subset_is_reproducible_for_a_seed() -> None:
    ids = [ex.question_id for ex in load_split(FIXTURE, "dev")]
    first = sample_subset(ids, 2, seed=42)
    assert first == sample_subset(ids, 2, seed=42)
    assert first == sample_subset(list(reversed(ids)), 2, seed=42)  # input order irrelevant
    assert len(first) == 2 and first == sorted(first)


def test_different_seed_gives_different_subset() -> None:
    ids = list(range(1034))
    assert sample_subset(ids, 200, seed=42) != sample_subset(ids, 200, seed=7)


def test_subset_pinned_values() -> None:
    # Pinned so a change to the sampling method cannot go unnoticed (it would change runs).
    assert sample_subset(range(1034), 5, seed=42) == [51, 228, 457, 501, 563]


@pytest.mark.parametrize("n", [0, 6])
def test_subset_size_out_of_range(n: int) -> None:
    with pytest.raises(ValueError):
        sample_subset(range(5), n, seed=42)


def test_subset_hash_depends_on_ids_and_order() -> None:
    assert subset_hash([1, 2, 3]) == subset_hash([1, 2, 3])
    assert subset_hash([1, 2, 3]) != subset_hash([1, 2, 4])
    assert len(subset_hash([])) == 64
