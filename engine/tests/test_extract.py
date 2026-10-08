import pytest

from qm_engine.extract import Extraction, extract


@pytest.mark.parametrize(
    ("reply", "sql"),
    [
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("```SQL\nSELECT 1;\n```", "SELECT 1;"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("```sqlite\nSELECT 1\n```", "SELECT 1"),
        ("```SELECT 1```", "SELECT 1"),
        (
            "Here you go:\n```sql\nSELECT name\nFROM singer\n```\nHope it helps!",
            "SELECT name\nFROM singer",
        ),
        ("SELECT count(*) FROM singer", "SELECT count(*) FROM singer"),
        ("  \n SELECT 1 \n ", "SELECT 1"),
        ("```sql\nSELECT 1 FROM t\nWHERE x = 2", "SELECT 1 FROM t\nWHERE x = 2"),  # cut off
    ],
    ids=[
        "fenced",
        "fenced-upper-tag",
        "fenced-no-tag",
        "fenced-other-tag",
        "one-line-fence",
        "fence-with-prose",
        "unfenced",
        "unfenced-whitespace",
        "unterminated-fence",
    ],
)
def test_extracts_sql(reply: str, sql: str) -> None:
    assert extract(reply, unanswerable_enabled=True) == Extraction("sql", sql=sql)


def test_multiple_fences_first_wins() -> None:
    reply = "```sql\nSELECT 1\n```\nor alternatively\n```sql\nSELECT 2\n```"
    assert extract(reply, unanswerable_enabled=False).sql == "SELECT 1"


@pytest.mark.parametrize(
    "reply",
    [
        "CANNOT_ANSWER: there is no salary column",
        "  cannot_answer: there is no salary column\n",
        "```\nCANNOT_ANSWER: there is no salary column\n```",
    ],
)
def test_cannot_answer_when_enabled(reply: str) -> None:
    assert extract(reply, unanswerable_enabled=True) == Extraction(
        "cannot_answer", reason="there is no salary column"
    )


def test_cannot_answer_when_disabled_is_treated_as_sql() -> None:
    # In eval the sentinel is off (design D6); the text then fails validation as a parse error.
    result = extract("CANNOT_ANSWER: no salary column", unanswerable_enabled=False)
    assert result == Extraction("sql", sql="CANNOT_ANSWER: no salary column")


@pytest.mark.parametrize("reply", ["", "   \n\t", "```sql\n```", "```\n   \n```"])
def test_empty_output(reply: str) -> None:
    assert extract(reply, unanswerable_enabled=True) == Extraction("empty")
