from pathlib import Path

import pytest

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


def test_default_index_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cfg_no_file(tmp_path, monkeypatch).index_paths == []


def test_default_exclude_patterns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cfg_no_file(tmp_path, monkeypatch).exclude_patterns == []


def test_default_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cfg_no_file(tmp_path, monkeypatch).db_path == Path.home() / ".oasis" / "index.db"


def test_default_vector_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cfg_no_file(tmp_path, monkeypatch).vector_path == Path.home() / ".oasis" / "vectors.lance"


def test_default_embedding_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cfg_no_file(tmp_path, monkeypatch).embedding_model == "all-MiniLM-L6-v2"


def test_default_llm_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cfg_no_file(tmp_path, monkeypatch).llm_provider == "anthropic"


# ---------------------------------------------------------------------------
# TOML loading
# ---------------------------------------------------------------------------


def test_loads_embedding_model_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_with_toml(tmp_path, 'embedding_model = "all-mpnet-base-v2"\n', monkeypatch)
    assert cfg.embedding_model == "all-mpnet-base-v2"


def test_loads_llm_provider_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_with_toml(tmp_path, 'llm_provider = "ollama"\n', monkeypatch)
    assert cfg.llm_provider == "ollama"


def test_loads_db_path_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_with_toml(tmp_path, f'db_path = "{tmp_path}/custom.db"\n', monkeypatch)
    assert cfg.db_path == tmp_path / "custom.db"


def test_loads_vector_path_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_with_toml(tmp_path, f'vector_path = "{tmp_path}/vecs.lance"\n', monkeypatch)
    assert cfg.vector_path == tmp_path / "vecs.lance"


def test_loads_index_paths_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = f'index_paths = ["{tmp_path}/docs", "{tmp_path}/notes"]\n'
    cfg = _cfg_with_toml(tmp_path, content, monkeypatch)
    assert cfg.index_paths == [tmp_path / "docs", tmp_path / "notes"]


def test_loads_exclude_patterns_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_with_toml(tmp_path, 'exclude_patterns = ["*.log", "scratch/"]\n', monkeypatch)
    assert cfg.exclude_patterns == ["*.log", "scratch/"]


def test_missing_toml_file_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_no_file(tmp_path, monkeypatch)
    assert cfg.embedding_model == "all-MiniLM-L6-v2"


def test_empty_toml_file_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_with_toml(tmp_path, "", monkeypatch)
    assert cfg.llm_provider == "anthropic"


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


def test_env_var_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OASIS_EMBEDDING_MODEL", "env-model")
    cfg = _cfg_no_file(tmp_path, monkeypatch)
    assert cfg.embedding_model == "env-model"


def test_env_var_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OASIS_EMBEDDING_MODEL", "env-model")
    cfg = _cfg_with_toml(tmp_path, 'embedding_model = "toml-model"\n', monkeypatch)
    assert cfg.embedding_model == "env-model"


def test_env_var_llm_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OASIS_LLM_PROVIDER", "ollama")
    cfg = _cfg_no_file(tmp_path, monkeypatch)
    assert cfg.llm_provider == "ollama"


def test_env_var_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OASIS_DB_PATH", str(tmp_path / "env.db"))
    cfg = _cfg_no_file(tmp_path, monkeypatch)
    assert cfg.db_path == tmp_path / "env.db"


def test_unprefixed_env_var_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "should-not-apply")
    cfg = _cfg_no_file(tmp_path, monkeypatch)
    assert cfg.embedding_model == "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# load_config convenience function
# ---------------------------------------------------------------------------


def test_load_config_returns_oasis_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "missing.toml")
    assert isinstance(load_config(), OasisConfig)


def test_load_config_respects_env_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OASIS_LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "missing.toml")
    assert load_config().llm_provider == "ollama"
