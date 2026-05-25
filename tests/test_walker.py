import types
from pathlib import Path

import pytest

from oasis.index.walker import walk


def _names(paths: list[Path]) -> list[str]:
    return sorted(p.name for p in paths)


def _rel(root: Path, paths: list[Path]) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in paths)


# ---------------------------------------------------------------------------
# Generator contract
# ---------------------------------------------------------------------------


def test_returns_generator(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    assert isinstance(walk(tmp_path), types.GeneratorType)


def test_empty_directory_yields_nothing(tmp_path: Path) -> None:
    assert list(walk(tmp_path)) == []


# ---------------------------------------------------------------------------
# Basic traversal
# ---------------------------------------------------------------------------


def test_yields_files_in_root(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.md").write_text("b")
    assert _names(list(walk(tmp_path))) == ["a.txt", "b.md"]


def test_yields_nested_files(tmp_path: Path) -> None:
    sub = tmp_path / "docs" / "api"
    sub.mkdir(parents=True)
    (sub / "reference.md").write_text("ref")
    (tmp_path / "readme.txt").write_text("top")
    assert _names(list(walk(tmp_path))) == ["readme.txt", "reference.md"]


def test_does_not_yield_directories(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    (tmp_path / "file.txt").write_text("x")
    result = list(walk(tmp_path))
    assert all(p.is_file() for p in result)


def test_output_is_sorted_within_directory(tmp_path: Path) -> None:
    for name in ("z.txt", "a.txt", "m.txt"):
        (tmp_path / name).write_text(name)
    result = list(walk(tmp_path))
    assert [p.name for p in result] == ["a.txt", "m.txt", "z.txt"]


# ---------------------------------------------------------------------------
# Hard-coded directory exclusions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dirname", [
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", "dist", "build", "target",
])
def test_excludes_named_directory(tmp_path: Path, dirname: str) -> None:
    excluded = tmp_path / dirname
    excluded.mkdir()
    (excluded / "noise.txt").write_text("noise")
    (tmp_path / "keep.txt").write_text("keep")
    result = list(walk(tmp_path))
    assert all(dirname not in str(p) for p in result)
    assert any(p.name == "keep.txt" for p in result)


def test_excludes_nested_pycache(tmp_path: Path) -> None:
    src = tmp_path / "src" / "__pycache__"
    src.mkdir(parents=True)
    (src / "module.cpython-314.pyc").write_bytes(b"")
    (tmp_path / "src" / "module.py").write_text("x = 1")
    result = list(walk(tmp_path))
    assert not any("__pycache__" in str(p) for p in result)
    assert any(p.name == "module.py" for p in result)


# ---------------------------------------------------------------------------
# Dotfile / dotdir exclusions
# ---------------------------------------------------------------------------


def test_excludes_dotfiles_by_default(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=x")
    (tmp_path / "readme.txt").write_text("hi")
    names = _names(list(walk(tmp_path)))
    assert ".env" not in names
    assert "readme.txt" in names


def test_excludes_dotdirs_by_default(tmp_path: Path) -> None:
    dotdir = tmp_path / ".hidden"
    dotdir.mkdir()
    (dotdir / "secret.txt").write_text("s")
    (tmp_path / "visible.txt").write_text("v")
    result = list(walk(tmp_path))
    assert all(".hidden" not in str(p) for p in result)
    assert any(p.name == "visible.txt" for p in result)


def test_includes_dotfiles_when_disabled(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("x")
    (tmp_path / "readme.txt").write_text("hi")
    names = _names(list(walk(tmp_path, exclude_dotfiles=False)))
    assert ".env" in names
    assert "readme.txt" in names


def test_includes_dotdirs_when_dotfiles_disabled(tmp_path: Path) -> None:
    dotdir = tmp_path / ".config"
    dotdir.mkdir()
    (dotdir / "settings.toml").write_text("[x]")
    names = _names(list(walk(tmp_path, exclude_dotfiles=False)))
    assert "settings.toml" in names


# ---------------------------------------------------------------------------
# extra_excludes
# ---------------------------------------------------------------------------


def test_extra_excludes_by_extension(tmp_path: Path) -> None:
    (tmp_path / "secret.key").write_text("key")
    (tmp_path / "readme.txt").write_text("hi")
    names = _names(list(walk(tmp_path, extra_excludes=["*.key"])))
    assert "secret.key" not in names
    assert "readme.txt" in names


def test_extra_excludes_by_directory(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "temp.txt").write_text("tmp")
    (tmp_path / "keep.txt").write_text("keep")
    names = _names(list(walk(tmp_path, extra_excludes=["scratch/"])))
    assert "temp.txt" not in names
    assert "keep.txt" in names


# ---------------------------------------------------------------------------
# .gitignore support
# ---------------------------------------------------------------------------


def test_respects_gitignore_file_pattern(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "app.log").write_text("log")
    (tmp_path / "readme.txt").write_text("readme")
    names = _names(list(walk(tmp_path, exclude_dotfiles=False)))
    assert "app.log" not in names
    assert "readme.txt" in names


def test_respects_gitignore_directory_pattern(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("scratch/\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "output.txt").write_text("out")
    (tmp_path / "keep.txt").write_text("keep")
    names = _names(list(walk(tmp_path, exclude_dotfiles=False)))
    assert "output.txt" not in names
    assert "keep.txt" in names


def test_gitignore_disabled(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "app.log").write_text("log")
    names = _names(list(walk(tmp_path, respect_gitignore=False, exclude_dotfiles=False)))
    assert "app.log" in names


def test_missing_gitignore_is_fine(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("hi")
    assert _names(list(walk(tmp_path))) == ["file.txt"]


# ---------------------------------------------------------------------------
# Default file pattern exclusions
# ---------------------------------------------------------------------------


def test_excludes_pyc_files(tmp_path: Path) -> None:
    (tmp_path / "module.pyc").write_bytes(b"\x00")
    (tmp_path / "module.py").write_text("x = 1")
    names = _names(list(walk(tmp_path)))
    assert "module.pyc" not in names
    assert "module.py" in names


def test_excludes_ds_store(tmp_path: Path) -> None:
    (tmp_path / ".DS_Store").write_bytes(b"\x00")
    (tmp_path / "file.txt").write_text("hi")
    # .DS_Store starts with '.' so it's caught by dotfile exclusion,
    # but the default pattern also covers it when exclude_dotfiles=False.
    names = _names(list(walk(tmp_path, exclude_dotfiles=False)))
    assert ".DS_Store" not in names
    assert "file.txt" in names
