"""Edge-case tests for the walker beyond the baseline test_walker.py coverage."""

from pathlib import Path

import pytest

from oasis.index.walker import walk


def _names(paths: list[Path]) -> list[str]:
    return sorted(p.name for p in paths)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_output_paths_are_absolute(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    for p in walk(tmp_path):
        assert p.is_absolute()


def test_output_paths_resolve_under_root(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "file.txt").write_text("x")
    for p in walk(tmp_path):
        assert str(p).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# Files with no extension
# ---------------------------------------------------------------------------


def test_no_extension_file_is_yielded(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:")
    (tmp_path / "README").write_text("readme")
    names = _names(list(walk(tmp_path)))
    assert "Makefile" in names
    assert "README" in names


def test_hidden_no_extension_excluded_by_default(tmp_path: Path) -> None:
    (tmp_path / ".profile").write_text("export PATH=...")
    (tmp_path / "README").write_text("ok")
    names = _names(list(walk(tmp_path)))
    assert ".profile" not in names
    assert "README" in names


# ---------------------------------------------------------------------------
# Symlinks — walker must not follow symlinks into directories
# ---------------------------------------------------------------------------


def test_symlink_to_directory_not_descended(tmp_path: Path) -> None:
    # source_dir lives outside the root that will be walked.
    # secret.txt is only reachable via the symlink — not via any real subdirectory.
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "secret.txt").write_text("secret content")

    root = tmp_path / "root"
    root.mkdir()
    (root / "visible.txt").write_text("visible")
    (root / "link_to_source").symlink_to(source_dir)

    names = _names(list(walk(root)))
    assert "visible.txt" in names
    assert "secret.txt" not in names


# ---------------------------------------------------------------------------
# Additional hard-coded directory exclusions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dirname", [".hg", ".svn", ".tox", ".eggs", ".ruff_cache"])
def test_excludes_additional_named_dirs(tmp_path: Path, dirname: str) -> None:
    excluded = tmp_path / dirname
    excluded.mkdir()
    (excluded / "noise.txt").write_text("noise")
    (tmp_path / "keep.txt").write_text("keep")
    result = list(walk(tmp_path))
    assert all(dirname not in str(p) for p in result)
    assert any(p.name == "keep.txt" for p in result)


def test_excludes_deeply_nested_pycache(tmp_path: Path) -> None:
    deep = tmp_path / "src" / "pkg" / "__pycache__"
    deep.mkdir(parents=True)
    (deep / "mod.cpython-314.pyc").write_bytes(b"")
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1")
    result = list(walk(tmp_path))
    assert not any("__pycache__" in str(p) for p in result)
    assert any(p.name == "mod.py" for p in result)


# ---------------------------------------------------------------------------
# Multiple extra_excludes
# ---------------------------------------------------------------------------


def test_multiple_extra_excludes_all_applied(tmp_path: Path) -> None:
    (tmp_path / "secret.key").write_text("key")
    (tmp_path / "output.log").write_text("log")
    (tmp_path / "readme.txt").write_text("keep")
    names = _names(list(walk(tmp_path, extra_excludes=["*.key", "*.log"])))
    assert "secret.key" not in names
    assert "output.log" not in names
    assert "readme.txt" in names


def test_empty_extra_excludes_list_is_fine(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("hi")
    assert len(list(walk(tmp_path, extra_excludes=[]))) == 1


def test_none_extra_excludes_is_fine(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("hi")
    assert len(list(walk(tmp_path, extra_excludes=None))) == 1


# ---------------------------------------------------------------------------
# .gitignore edge cases
# ---------------------------------------------------------------------------


def test_gitignore_with_comments_and_blank_lines(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("# comment\n\n*.log\n\n# another\n")
    (tmp_path / "app.log").write_text("log")
    (tmp_path / "readme.txt").write_text("keep")
    names = _names(list(walk(tmp_path, exclude_dotfiles=False)))
    assert "app.log" not in names
    assert "readme.txt" in names


def test_gitignore_read_error_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "readme.txt").write_text("hi")

    original = Path.read_text

    def bad_read(self: Path, **kwargs: object) -> str:
        if self.name == ".gitignore":
            raise OSError("permission denied")
        return original(self, **kwargs)

    monkeypatch.setattr(Path, "read_text", bad_read)
    names = _names(list(walk(tmp_path, exclude_dotfiles=False)))
    assert "readme.txt" in names


def test_gitignore_negation_pattern_included(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n!important.log\n")
    (tmp_path / "noise.log").write_text("noise")
    (tmp_path / "important.log").write_text("keep")
    names = _names(list(walk(tmp_path, exclude_dotfiles=False)))
    assert "noise.log" not in names
    assert "important.log" in names


# ---------------------------------------------------------------------------
# Output is a generator (lazy evaluation)
# ---------------------------------------------------------------------------


def test_walk_is_lazy_generator(tmp_path: Path) -> None:
    import types
    (tmp_path / "a.txt").write_text("a")
    assert isinstance(walk(tmp_path), types.GeneratorType)


def test_walk_single_file_at_root(tmp_path: Path) -> None:
    (tmp_path / "only.txt").write_text("only")
    result = list(walk(tmp_path))
    assert len(result) == 1
    assert result[0].name == "only.txt"
