import pytest

from oasis.cli.app import _human_size


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"),
    (1, "1 B"),
    (512, "512 B"),
    (1023, "1023 B"),
    # Exactly 1 KB
    (1024, "1.0 KB"),
    (1536, "1.5 KB"),
    (1024 * 1023, "1023.0 KB"),
    # Exactly 1 MB
    (1024 ** 2, "1.0 MB"),
    (int(1.5 * 1024 ** 2), "1.5 MB"),
    # Exactly 1 GB
    (1024 ** 3, "1.0 GB"),
    (int(2.5 * 1024 ** 3), "2.5 GB"),
    # TB (falls out of the for-loop)
    (1024 ** 4, "1.0 TB"),
    (2 * 1024 ** 4, "2.0 TB"),
])
def test_human_size(n: int, expected: str) -> None:
    assert _human_size(n) == expected


def test_returns_string() -> None:
    assert isinstance(_human_size(42), str)


def test_bytes_unit_has_no_decimal(n: int = 999) -> None:
    result = _human_size(999)
    assert "." not in result
    assert "B" in result


def test_non_byte_units_have_one_decimal() -> None:
    assert _human_size(1024) == "1.0 KB"
    assert _human_size(1024 ** 2) == "1.0 MB"
