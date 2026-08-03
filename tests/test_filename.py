from pathlib import Path

import pytest

from oasis.index.filename import filename_words, humanize_filename


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("q3-report.pdf", "q3 report"),
        ("q3_report_final.pdf", "q3 report final"),
        ("paper-okapi-at-trec3.pdf", "paper okapi at trec 3"),
        ("seaborn-anscombe.csv", "seaborn anscombe"),
        ("Q3ReportFinal.pdf", "Q3 Report Final"),
        ("HTTPServerNotes.md", "HTTP Server Notes"),
        ("2023taxes.pdf", "2023 taxes"),
        ("meeting notes (draft).docx", "meeting notes draft"),
        ("budget.v2.final.xlsx", "budget v2 final"),
    ],
)
def test_humanizes_real_world_names(name: str, expected: str) -> None:
    assert humanize_filename(name) == expected


def test_drops_directories() -> None:
    # Folder tokens belong to the `path` FTS column. Folding them in here would
    # put every token of the user's home directory in the top-weighted column
    # of every document they own.
    assert humanize_filename("/Users/you/Documents/tax/q3-report.pdf") == "q3 report"


def test_drops_only_the_last_suffix() -> None:
    assert humanize_filename("backup.tar.gz") == "backup tar"


def test_extensionless_name_is_kept_whole() -> None:
    # No suffix to strip — "Makefile" must not be mistaken for an extension.
    assert humanize_filename("Makefile") == "Makefile"
    assert humanize_filename("README") == "README"


def test_dotfile_keeps_its_name() -> None:
    # Path(".zshrc").suffix is "", so nothing is stripped.
    assert humanize_filename(".zshrc") == "zshrc"


def test_accepts_path_objects() -> None:
    assert humanize_filename(Path("/tmp/annual_report_2024.docx")) == "annual report 2024"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("q3", ["q3"]),          # short label + digits is one word
        ("v2", ["v2"]),
        ("mp3", ["mp3"]),
        ("trec3", ["trec", "3"]),  # long word + digits is two
        ("covid19", ["covid", "19"]),
        ("2023taxes", ["2023", "taxes"]),  # digits then letters never glue
        ("q-3", ["q", "3"]),     # a separator the user typed is a boundary
    ],
)
def test_letter_digit_gluing(name: str, expected: list[str]) -> None:
    assert filename_words(name) == expected


def test_unicode_words_survive() -> None:
    # \W would be locale-dependent; the separator class is explicit ASCII
    # alphanumerics, so non-ASCII letters split. Documented behavior, not a
    # silent one: CJK and accented names still yield their own tokens.
    assert filename_words("café-notes") == ["caf", "notes"]


def test_punctuation_only_name_yields_nothing() -> None:
    assert humanize_filename("---.txt") == ""
    assert filename_words("___") == []


def test_empty_string_is_safe() -> None:
    assert humanize_filename("") == ""
    assert filename_words("") == []
