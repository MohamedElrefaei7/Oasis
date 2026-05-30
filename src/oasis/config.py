from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

# Module-level so tests can monkeypatch it before constructing OasisConfig.
CONFIG_PATH: Path = Path.home() / ".config" / "oasis" / "config.toml"


class OasisConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OASIS_",
    )

    db_path: Path = Path.home() / ".oasis" / "index.db"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority: explicit kwargs > env vars > TOML file > field defaults.
        # CONFIG_PATH is read here (at construction time) so monkeypatching it
        # before OasisConfig() is called works in tests.
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=CONFIG_PATH),
        )


def load_config() -> OasisConfig:
    """Load config from ~/.config/oasis/config.toml with env var overrides."""
    return OasisConfig()
