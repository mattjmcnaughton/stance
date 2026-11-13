"""Application configuration: logging settings and the XDG user config file."""

import math
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    log_level: str = "WARNING"
    log_format: str = "console"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()


class ConfigError(ValueError):
    """Invalid user configuration or CLI input."""


def validate_budget(value: float, source: str) -> float:
    """Return *value* if it is a positive, finite USD amount."""
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ConfigError(
            f"{source} must be a positive, finite USD amount; got {value!r}."
        )
    return float(value)


class _ServerSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = None


class _HarnessSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None


class UserConfig(BaseModel):
    """Contents of ``config.toml``. Every field is optional."""

    model_config = ConfigDict(extra="forbid")

    budget_usd: float | None = None
    server: _ServerSection = _ServerSection()
    claude: _HarnessSection = _HarnessSection()
    codex: _HarnessSection = _HarnessSection()

    @field_validator("budget_usd", mode="before")
    @classmethod
    def _budget(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("must be a number")
        return validate_budget(float(value), "budget_usd")


def config_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return ``$XDG_CONFIG_HOME/stance/config.toml`` (default ``~/.config``)."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME", "~")) / ".config")
    return Path(base).expanduser() / "stance" / "config.toml"


def load_user_config(path: Path | None = None) -> UserConfig:
    """Load and validate the user config file; a missing file means defaults."""
    path = path or config_path()
    if not path.exists():
        return UserConfig()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        return UserConfig.model_validate(raw)
    except (tomllib.TOMLDecodeError, ValidationError, ConfigError, OSError) as exc:
        raise ConfigError(f"Invalid Stance config {path}: {exc}") from exc
