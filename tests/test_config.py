from pathlib import Path

import pytest
from pydantic import ValidationError

import oasis.config as config_module
from oasis.config import CONFIG_PATH, OasisConfig, load_config


def _cfg_with_toml(tmp_path: Path, content: str, monkeypatch: pytest.MonkeyPatch) -> OasisConfig:
    toml = tmp_path / "config.toml"
    toml.write_text(content)
    monkeypatch.setattr(config_module, "CONFIG_PATH", toml)
    return OasisConfig()


def _cfg_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OasisConfig:
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "missing.toml")
    return OasisConfig()


# ---------------------------------------------------------------------------
# Config path constant
# ---------------------------------------------------------------------------


def test_config_path_location() -> None:
    assert CONFIG_PATH == Path.home() / ".config" / "oasis" / "config.toml"


# ---------------------------------------------------------------------------
# Defaults (no file, no env vars)
# ---------------------------------------------------------------------------


def test_default_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cfg_no_file(tmp_path, monkeypatch).db_path == Path.home() / ".oasis" / "index.db"


# ---------------------------------------------------------------------------
# TOML loading
# ---------------------------------------------------------------------------


def test_loads_db_path_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_with_toml(tmp_path, f'db_path = "{tmp_path}/custom.db"\n', monkeypatch)
    assert cfg.db_path == tmp_path / "custom.db"


def test_missing_toml_file_uses_default_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_no_file(tmp_path, monkeypatch)
    assert cfg.db_path == Path.home() / ".oasis" / "index.db"


def test_empty_toml_file_uses_default_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_with_toml(tmp_path, "", monkeypatch)
    assert cfg.db_path == Path.home() / ".oasis" / "index.db"


def test_unknown_toml_field_raises_validation_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _cfg_with_toml(tmp_path, 'unknown_field = "x"\n', monkeypatch)


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


def test_env_var_overrides_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OASIS_DB_PATH", str(tmp_path / "env.db"))
    cfg = _cfg_no_file(tmp_path, monkeypatch)
    assert cfg.db_path == tmp_path / "env.db"


def test_env_var_wins_over_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OASIS_DB_PATH", str(tmp_path / "env.db"))
    cfg = _cfg_with_toml(tmp_path, f'db_path = "{tmp_path}/toml.db"\n', monkeypatch)
    assert cfg.db_path == tmp_path / "env.db"


def test_unprefixed_env_var_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "env.db"))
    cfg = _cfg_no_file(tmp_path, monkeypatch)
    assert cfg.db_path == Path.home() / ".oasis" / "index.db"


# ---------------------------------------------------------------------------
# load_config convenience function
# ---------------------------------------------------------------------------


def test_load_config_returns_oasis_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "missing.toml")
    assert isinstance(load_config(), OasisConfig)


def test_load_config_respects_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OASIS_DB_PATH", str(tmp_path / "env.db"))
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "missing.toml")
    assert load_config().db_path == tmp_path / "env.db"
